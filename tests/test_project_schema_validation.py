import unittest

from pydantic import ValidationError

from app.schemas.project import ProjectCreate, ProjectUpdate


class TestProjectSchemaValidation(unittest.TestCase):
    def test_create_trims_and_accepts_valid_values(self):
        payload = ProjectCreate(
            name="  我的项目  ",
            intro="  简介  ",
            type="  都市爱情  ",
            content_type="web_novel",
            art_style="  漫画风格  ",
            video_ratio=" 9:16 ",
        )
        self.assertEqual(payload.name, "我的项目")
        self.assertEqual(payload.intro, "简介")
        self.assertEqual(payload.type, "都市爱情")
        self.assertEqual(payload.content_type, "web_novel")
        self.assertEqual(payload.art_style, "漫画风格")
        self.assertEqual(payload.video_ratio, "9:16")

    def test_create_rejects_empty_name(self):
        with self.assertRaises(ValidationError):
            ProjectCreate(name="   ")

    def test_create_rejects_invalid_video_ratio(self):
        with self.assertRaises(ValidationError):
            ProjectCreate(name="ok", video_ratio="abc")

    def test_update_rejects_empty_name(self):
        with self.assertRaises(ValidationError):
            ProjectUpdate(name=" ")

    def test_update_rejects_invalid_video_ratio(self):
        with self.assertRaises(ValidationError):
            ProjectUpdate(video_ratio="1:1")
