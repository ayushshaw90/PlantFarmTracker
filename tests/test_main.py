import unittest

import main


class MainLauncherTests(unittest.TestCase):
    def test_build_commands_includes_backend_dashboard_and_optional_publisher(self):
        commands = main.build_commands(include_publisher=True)

        self.assertEqual(commands[0][0], "python")
        self.assertIn("Backend-server/server.py", commands[0][1])
        self.assertIn("panel", commands[1][0])
        self.assertIn("Client/dashboard.py", commands[1][1])
        self.assertIn("MQTT-server/publisher.py", commands[2][1])

    def test_build_commands_can_disable_publisher(self):
        commands = main.build_commands(include_publisher=False)
        self.assertEqual(len(commands), 2)


if __name__ == "__main__":
    unittest.main()
