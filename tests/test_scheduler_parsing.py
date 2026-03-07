import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.executor import TaskSchedulerExecutor
from scheduler.management import parse_schedule_to_next_run


class TestSchedulerParsing(unittest.TestCase):
    def test_parse_daily_schedule_with_am_pm_hour_only(self):
        next_run = parse_schedule_to_next_run("every day at 1am")
        self.assertIsNotNone(next_run)
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        self.assertEqual(parsed.hour, 1)
        self.assertEqual(parsed.minute, 0)

    def test_parse_daily_schedule_with_am_pm_and_minutes(self):
        next_run = parse_schedule_to_next_run("Every day at 1:00 AM")
        self.assertIsNotNone(next_run)
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        self.assertEqual(parsed.hour, 1)
        self.assertEqual(parsed.minute, 0)

    def test_parse_daily_schedule_24_hour_time(self):
        next_run = parse_schedule_to_next_run("every day at 14:30")
        self.assertIsNotNone(next_run)
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        self.assertEqual(parsed.hour, 14)
        self.assertEqual(parsed.minute, 30)

    def test_parse_interval_schedule_weeks(self):
        next_run = parse_schedule_to_next_run("every 2 weeks")
        self.assertIsNotNone(next_run)

    def test_executor_calculate_next_run_delegates_daily_time_schedule(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            jobs_file = base / "jobs.json"
            logs_dir = base / "logs"
            results_dir = base / "results"
            jobs_file.write_text('{"jobs": []}')
            logs_dir.mkdir()
            results_dir.mkdir()

            with unittest.mock.patch.dict(
                "os.environ",
                {
                    "SCHEDULER_JOBS_FILE": str(jobs_file),
                    "SCHEDULER_LOGS_DIR": str(logs_dir),
                    "SCHEDULER_RESULTS_DIR": str(results_dir),
                },
                clear=False,
            ):
                executor = TaskSchedulerExecutor()
                next_run = executor._calculate_next_run("every day at 1am")

        self.assertIsNotNone(next_run)
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        self.assertEqual(parsed.hour, 1)
        self.assertEqual(parsed.minute, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
