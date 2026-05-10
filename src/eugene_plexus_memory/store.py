"""In-process conversation store.

v0.1 ships a thread-safe dict implementation. The HTTP shape it serves
is the long-term contract; only this backend is expected to change.

Caps from runtime config (`maxConversations`, `maxMessagesPerConversation`)
are read on every operation so the operator can tune them via the UI
without restarting. Eviction is FIFO: oldest conversations / oldest
messages within a conversation are dropped when limits are exceeded.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from ._generated.models import Conversation, Message


class InProcessStore:
    """Thread-safe in-memory store of conversations keyed by uuid.

    `limits` is a callable rather than a snapshot so tuning maxConversations /
    maxMessagesPerConversation through `PATCH /v1/config` takes effect on
    the next operation without requiring the routes to re-inject the store.
    """

    def __init__(self, limits: Callable[[], tuple[int, int]]) -> None:
        self._lock = threading.Lock()
        self._conversations: OrderedDict[UUID, list[Message]] = OrderedDict()
        self._limits = limits

    def create(self) -> Conversation:
        cid = uuid4()
        with self._lock:
            self._conversations[cid] = []
            self._evict_locked()
            return Conversation(id=cid, messages=[])

    def get(self, conversation_id: UUID) -> Conversation | None:
        with self._lock:
            messages = self._conversations.get(conversation_id)
            if messages is None:
                return None
            return Conversation(id=conversation_id, messages=list(messages))

    def append(self, conversation_id: UUID, message: Message) -> Message | None:
        """Append a message. Returns the stored Message (timestamp may be filled
        in) or None if the conversation does not exist."""
        with self._lock:
            messages = self._conversations.get(conversation_id)
            if messages is None:
                return None

            stamped = _ensure_timestamp(message)
            messages.append(stamped)

            # Trim oldest messages if we exceed the per-conversation cap.
            _, max_messages = self._limits()
            if len(messages) > max_messages:
                del messages[: len(messages) - max_messages]

            # LRU-style: appended-to conversations move to the end so eviction
            # always drops the conversation that's been quiet the longest.
            self._conversations.move_to_end(conversation_id)
            return stamped

    def delete(self, conversation_id: UUID) -> bool:
        with self._lock:
            return self._conversations.pop(conversation_id, None) is not None

    def _evict_locked(self) -> None:
        max_conversations, _ = self._limits()
        while len(self._conversations) > max_conversations:
            self._conversations.popitem(last=False)


def _ensure_timestamp(message: Message) -> Message:
    if message.timestamp is not None:
        return message
    return message.model_copy(update={"timestamp": datetime.now(UTC)})
