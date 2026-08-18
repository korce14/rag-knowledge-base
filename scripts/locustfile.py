import os
from locust import HttpUser, between, task


class RAGUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        response = self.client.post("/api/auth/login", json={"username": os.getenv("RAG_USERNAME", "korce"), "password": os.getenv("RAG_PASSWORD", "change-me")})
        self.token = response.json().get("token", "")
        self.kb_id = ""
        if self.token:
            kbs = self.client.get("/api/knowledge_bases", headers=self.auth_headers()).json()
            if kbs:
                self.kb_id = kbs[0]["id"]

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    @task(4)
    def ask_question(self):
        if not self.kb_id:
            return
        self.client.post(
            "/api/chat",
            json={"kb_id": self.kb_id, "question": "802.15.4 有哪些实验步骤？", "session_id": f"load_{self._get_id()}"},
            headers=self.auth_headers(),
        )

    @task(1)
    def agent_calculate(self):
        if not self.kb_id:
            return
        self.client.post(
            "/api/agent",
            json={"tool": "calculate", "args": {"expression": "(3+5)*2-4"}},
            headers=self.auth_headers(),
        )

    def _get_id(self):
        import random

        return random.randint(1, 100000)
