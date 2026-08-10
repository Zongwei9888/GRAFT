from __future__ import annotations

import json
import sys


prompt = sys.stdin.read()
is_resume = "resume" in sys.argv
thread_id = "thread-resumed" if is_resume else "thread-new"
response = {
    "verdict": "pass",
    "failure_modes": [],
    "summary": "Fake reviewer found no concrete failure.",
    "evidence": [],
    "confidence": 0.5,
}

print(json.dumps({"type": "thread.started", "thread_id": thread_id}))
print(json.dumps({"type": "turn.started"}))
print(
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "agent_message",
                "text": json.dumps(response),
            },
        }
    )
)
print(
    json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": len(prompt), "output_tokens": 10},
        }
    )
)
