import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from fastapi.testclient import TestClient
    import server
except Exception:  # fastapi not installed in minimal CI
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi unavailable")
class BadJobIds(unittest.TestCase):
    def test_malformed_ids_are_404_not_500(self):
        c = TestClient(server.app)
        for jid in ("..", "a%5Cb", "bad%20id", "x%2e%2e"):
            self.assertEqual(c.get("/api/jobs/" + jid).status_code, 404, jid)
            self.assertEqual(c.get("/api/jobs/%s/media/f.mp4" % jid).status_code, 404, jid)


if __name__ == "__main__":
    unittest.main()
