from __future__ import annotations

import inspect


class NativeZenClient:
    """Small App Server client fake shared by adapter and Gateway evidence."""

    def __init__(self) -> None:
        self.handlers = []
        self.reset_handlers = []
        self.started_threads = []
        self.started_turns = []
        self.resumed_threads = []
        self.threads = {}

    def add_notification_handler(self, handler) -> None:
        self.handlers.append(handler)

    def add_connection_reset_handler(self, handler) -> None:
        self.reset_handlers.append(handler)

    async def reset_connection(self, connection_epoch: int = 1) -> None:
        for handler in tuple(self.reset_handlers):
            result = handler(connection_epoch)
            if inspect.isawaitable(result):
                await result

    def local_image_paths_epoch(self) -> int:
        return 1

    async def list_threads(self, **_params):
        return {"data": list(self.threads.values())}

    async def list_thread_turns(self, thread_id: str, **_params):
        return {
            "data": [
                {
                    "id": "turn-live",
                    "status": "inProgress",
                    "items": [
                        {
                            "id": "user-live",
                            "type": "userMessage",
                            "text": "Refactor the adapters",
                        },
                        {
                            "id": "progress-1",
                            "type": "agentMessage",
                            "phase": "commentary",
                            "text": "Inspecting the existing adapters.",
                        },
                        {
                            "id": "progress-2",
                            "type": "agentMessage",
                            "phase": "commentary",
                            "text": "Running the focused tests.",
                        },
                    ],
                },
                {
                    "id": "turn-old",
                    "status": "completed",
                    "items": [
                        {
                            "id": "user-old",
                            "type": "userMessage",
                            "text": "Design the SDK",
                        },
                        {
                            "id": "assistant-old",
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": "The SDK design is complete.",
                        },
                    ],
                },
            ],
            "nextCursor": None,
        }

    async def start_thread(self, **params):
        self.started_threads.append(params)
        thread = {
            "id": f"zen-thread-{len(self.started_threads)}",
            "cwd": params["cwd"],
            "preview": "",
            "status": {"type": "idle"},
        }
        self.threads[thread["id"]] = thread
        return {"thread": thread}

    async def read_thread(self, thread_id: str, *, include_turns: bool = False):
        return {
            "thread": {
                "id": thread_id,
                "cwd": "/repo",
                "preview": "Build the SDK",
                "status": {"type": "idle"},
                "turns": [] if include_turns else None,
            }
        }

    async def resume_thread(self, **params):
        self.resumed_threads.append(str(params["threadId"]))
        return await self.read_thread(str(params["threadId"]))

    async def start_turn(
        self,
        thread_id: str,
        text: str | None = None,
        **params,
    ):
        if text is None:
            text = str(params)
        self.started_turns.append((thread_id, text))
        await self._notify(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "item": {
                        "id": "assistant-1",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "## Done\n\n**Markdown** is enabled.",
                    },
                },
            }
        )
        await self._notify(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        return {"turn": {"id": "turn-1"}}

    async def interrupt_turn(self, thread_id: str, turn_id: str):
        return {"threadId": thread_id, "turnId": turn_id}

    async def _notify(self, notification) -> None:
        for handler in self.handlers:
            result = handler(notification)
            if inspect.isawaitable(result):
                await result
