"""Export selection and packaging tests."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import ACCEPTED
from core.store import Store
from server import routes_project as rp


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def accept(self, title, result, depends_on=None):
        task = self.store.add_task(title, f"Complete {title}", depends_on or [])
        task.state = ACCEPTED
        task.result = result
        return task

    def test_artifact_requires_project_and_accepted_output(self):
        code, body = rp.export_project(self.store, {}, {"format": "artifact"})
        self.assertEqual(code, 404)
        self.assertIn("Create a project", body["error"])

        self.store.create_project("Build a polished demo")
        self.store.add_task("Research", "Find examples")
        code, body = rp.export_project(self.store, {}, {"format": "artifact"})
        self.assertEqual(code, 409)
        self.assertIn("Accept at least one", body["error"])

    def test_prefers_integrated_leaf_and_exports_clean_html(self):
        self.store.create_project("Build a Launch Page!")
        component = self.accept("Create hero component", "<section>Hero</section>")
        final = self.accept(
            "Integrate final deliverable",
            "```html\n<!doctype html><html><body>Ready</body></html>\n```",
            [component.id],
        )

        code, body = rp.export_project(self.store, {}, {"format": "artifact"})
        self.assertEqual(code, 200)
        self.assertEqual(body["filename"], "build-a-launch-page.html")
        self.assertEqual(body["mime_type"], "text/html;charset=utf-8")
        self.assertEqual(body["source_task"]["id"], final.id)
        self.assertTrue(body["content"].startswith("<!doctype html>"))
        self.assertNotIn("```", body["content"])
        self.assertEqual(body["accepted_count"], 2)

    def test_record_is_available_before_final_result(self):
        project = self.store.create_project("API prototype")
        task = self.store.add_task("Define contract", "Write the API contract")

        code, body = rp.export_project(self.store, {}, {"format": "record"})
        self.assertEqual(code, 200)
        self.assertEqual(body["filename"], "api-prototype-consortium.json")
        record = json.loads(body["content"])
        self.assertEqual(record["schema"], "consortium.project.v1")
        self.assertEqual(record["project"]["id"], project.id)
        self.assertEqual(record["tasks"][0]["id"], task.id)
        self.assertIsNone(record["final_task_id"])

    def test_json_output_keeps_json_extension(self):
        self.store.create_project("Return data")
        self.accept("Final result", '{"ready": true}')
        code, body = rp.export_project(self.store, {}, {})
        self.assertEqual(code, 200)
        self.assertEqual(body["filename"], "return-data.json")
        self.assertEqual(json.loads(body["content"]), {"ready": True})

    def test_html_fragment_exports_as_html(self):
        self.store.create_project("Component demo")
        self.accept("Final component", "<main><h1>Ready</h1></main>")
        code, body = rp.export_project(self.store, {}, {})
        self.assertEqual(code, 200)
        self.assertEqual(body["filename"], "component-demo.html")

    def test_rejects_unknown_format(self):
        self.store.create_project("Demo")
        code, body = rp.export_project(self.store, {}, {"format": "zip"})
        self.assertEqual(code, 400)
        self.assertIn("format", body["error"])


if __name__ == "__main__":
    unittest.main()
