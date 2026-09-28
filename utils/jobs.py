"""ETL job queue. Redis when REDIS_URL is set; memory stand-in otherwise."""

import uuid


class JobQueue:
    QUEUE = "qm:jobs"

    def __init__(self, client):
        self.client = client

    def enqueue(self, *, question: str, user_id: str, tenant_id: str, role: str) -> str:
        job_id = str(uuid.uuid4())
        self.client.hset(
            f"qm:job:{job_id}",
            mapping={
                "id": job_id,
                "question": question,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "role": role,
                "status": "queued",
                "result": "",
                "error": "",
            },
        )
        self.client.lpush(self.QUEUE, job_id)
        return job_id

    def get(self, job_id: str) -> dict | None:
        data = self.client.hgetall(f"qm:job:{job_id}")
        return data or None

    def update(self, job_id: str, **fields) -> None:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        current.update({key: "" if value is None else str(value) for key, value in fields.items()})
        self.client.hset(f"qm:job:{job_id}", mapping=current)

    def pop(self, timeout: int = 5) -> dict | None:
        item = self.client.brpop(self.QUEUE, timeout=timeout)
        if not item:
            return None
        job_id = item[1]
        return self.get(job_id)


def process_next(store: JobQueue, runner, timeout: int = 1) -> str | None:
    """Claim one job and run it outside the API process."""
    job = store.pop(timeout)
    if job is None:
        return None
    job_id = job["id"]
    store.update(job_id, status="running")
    try:
        result = runner(job)
    except Exception as exc:
        store.update(job_id, status="failed", error=str(exc)[:500])
        return job_id
    store.update(job_id, status="succeeded", result=str(result)[:4000])
    return job_id
