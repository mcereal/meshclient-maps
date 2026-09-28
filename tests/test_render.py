import os
import subprocess
import tempfile
import unittest
from unittest import mock

from meshmaps import render


class Extract(unittest.TestCase):
    def run_extract(self, outcomes):
        calls = []

        def run(command, check):
            calls.append(command)
            outcome = outcomes[len(calls) - 1]
            if outcome != 0:
                raise subprocess.CalledProcessError(outcome, command)
            with open(command[3], "w") as handle:
                handle.write("tiles")

        directory = tempfile.mkdtemp()
        output = os.path.join(directory, "x.pmtiles")
        with (
            mock.patch.object(render.subprocess, "run", run),
            mock.patch.object(render.time, "sleep") as slept,
            mock.patch.object(render, "pmtiles_tool", return_value="pmtiles"),
        ):
            try:
                render.extract(directory, "https://planet", None, 13, output)
            finally:
                done = os.path.exists(output)
        return calls, slept, done

    def test_a_reset_stream_is_tried_again_after_a_pause(self):
        calls, slept, done = self.run_extract([1, 1, 0])
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.args[0] for c in slept.call_args_list], [60, 120])
        self.assertTrue(done)

    def test_it_gives_up_after_the_last_attempt(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_extract([1] * render.EXTRACT_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
