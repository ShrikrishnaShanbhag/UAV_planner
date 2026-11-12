"""WebSocket streaming of a simulation, with play, pause, seek and event injection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from uavplan.api.sessions import SimulationSession
from uavplan.core.models import Event

router = APIRouter()
FRAME_SECONDS = 0.1
DEFAULT_SPEED = 10.0


@dataclass(slots=True)
class Playback:
    t: float = 0.0
    playing: bool = False
    speed: float = DEFAULT_SPEED


def _parse_event(payload: dict[str, Any], now: float) -> Event:
    return Event.from_dict({"time": now, **payload})


async def _handle(
    message: dict[str, Any], session: SimulationSession, state: Playback, socket: WebSocket
) -> None:
    command = message.get("cmd")
    payload = message.get("payload") or {}
    if command == "play":
        state.playing = True
    elif command == "pause":
        state.playing = False
    elif command == "speed":
        state.speed = max(0.1, float(payload.get("value", DEFAULT_SPEED)))
    elif command == "seek":
        target = min(max(float(payload.get("t", 0.0)), 0.0), session.end_time)
        await socket.send_json(session.frame(target, target, seeked=True, playing=state.playing))
        state.t = target
    elif command == "inject_event":
        event = _parse_event(payload, state.t)
        before = state.t
        await asyncio.to_thread(session.inject, event)
        # Report the replan immediately, including one at the current instant.
        t = max(event.time, before)
        await socket.send_json(session.frame(min(before, t - 1e-9), t, playing=state.playing))
        state.t = t
    else:
        await socket.send_json({"type": "error", "message": f"unknown command '{command}'"})


@router.websocket("/ws/simulations/{sim_id}")
async def stream(socket: WebSocket, sim_id: str) -> None:
    session: SimulationSession | None = socket.app.state.sessions.get(sim_id)
    await socket.accept()
    if session is None:
        await socket.send_json({"type": "error", "message": f"no simulation '{sim_id}'"})
        await socket.close(code=4404)
        return
    state = Playback()
    await socket.send_json(session.frame(-1.0, 0.0, playing=False))
    receiver = asyncio.ensure_future(socket.receive_json())
    try:
        while True:
            done, _ = await asyncio.wait({receiver}, timeout=FRAME_SECONDS)
            if receiver in done:
                try:
                    await _handle(receiver.result(), session, state, socket)
                except (KeyError, ValueError, TypeError) as error:
                    await socket.send_json({"type": "error", "message": str(error)})
                receiver = asyncio.ensure_future(socket.receive_json())
            if state.playing:
                previous = state.t
                state.t = min(previous + state.speed * FRAME_SECONDS, session.end_time)
                state.playing = state.t < session.end_time
                await socket.send_json(session.frame(previous, state.t, playing=state.playing))
    except WebSocketDisconnect:
        pass
    finally:
        receiver.cancel()
