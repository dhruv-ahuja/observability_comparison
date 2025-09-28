"""
Locust test file for observability comparison.

To run this with 5-20 concurrent users:
locust -f locustfile.py --headless -u 20 -r 2 --run-time 5m

Where:
-u 20: maximum number of users (set to 20)
-r 2: spawn rate (how many users to spawn per second)
--run-time 5m: how long to run the test for
"""

from locust import HttpUser, TaskSet, task, between


class UserBehavior(TaskSet):
    @task(3)
    def fast_endpoint(self):
        self.client.get("/fast")

    @task(2)
    def slow_endpoint(self):
        self.client.get("/slow")

    @task(1)
    def error_endpoint(self):
        self.client.get("/error")


class WebsiteUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = between(1, 2)
    tasks = [UserBehavior]
