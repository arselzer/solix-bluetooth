from types import SimpleNamespace

from solix_gen2 import Model
from solix_gen2 import cli, interactive
from solix_gen2.config import DeviceConfig, load_config, save_config


def inputs(monkeypatch, values):
    values = iter(values)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(values))


def test_no_arguments_launch_guided_mode_only_in_a_terminal(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(interactive, "run_interactive", lambda config, directory: called.append((config, directory)))
    assert cli.main([]) == 0
    assert called == [(cli.DEFAULT_CONFIG, None)]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main([]) == 2


def test_explicit_interactive_config_and_private_directory(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(interactive, "run_interactive", lambda config, directory: called.append((config, directory)))
    config, directory = tmp_path / "config.json", tmp_path / "private"
    assert cli.main(["interactive", "--config", str(config), "--lab-directory", str(directory)]) == 0
    assert called == [(config, directory)]


def test_menu_invalid_selection_and_back(monkeypatch):
    inputs(monkeypatch, ["", "-1", "3", "2"])
    assert interactive.choose("Menu", ["one", "two"]) == 1
    inputs(monkeypatch, ["0"])
    assert interactive.choose("Menu", ["one"]) is None


def test_scan_merges_saved_devices_and_saves_new_station(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    saved = DeviceConfig("ups", "AA:BB:CC:DD:EE:01", Model.C2000_GEN2, "a" * 40)
    save_config([saved], config)
    async def scan(timeout):
        return [SimpleNamespace(address=saved.address, name="Anker A1783"),
                SimpleNamespace(address="AA:BB:CC:DD:EE:02", name="Anker A1723")]
    monkeypatch.setattr(interactive, "discover", scan)
    inputs(monkeypatch, ["2", "c300"])
    selected = interactive.select_device(config)
    assert selected.model == Model.C300 and selected.protocol == "legacy"
    assert selected.client_id is None
    assert len(load_config(config)) == 2
    assert config.stat().st_mode & 0o777 == 0o600


def test_saved_selection_survives_missing_bluetooth(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    saved = DeviceConfig("original", "AA:BB:CC:DD:EE:03", Model.C1000)
    save_config([saved], config)
    async def scan(timeout):
        raise OSError("No adapter")
    monkeypatch.setattr(interactive, "discover", scan)
    inputs(monkeypatch, ["1"])
    assert interactive.select_device(config) == saved
    assert interactive.ensure_paired(saved, config) == saved


def test_nonroot_native_setup_prints_explicit_command_without_spawning(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(interactive.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(interactive.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")))
    interactive.native_session(tmp_path / "private directory", tmp_path / "config.json", provision=True, allow_control=False)
    output = capsys.readouterr().out
    assert "sudo " in output and "--provision" in output
    assert "--allow-control" not in output
    assert "private directory'" in output  # The displayed path is shell-quoted.


def test_interactive_native_session_stops_owned_child(monkeypatch, tmp_path):
    import signal
    directory = tmp_path / "lab"
    directory.mkdir(mode=0o700)
    spawned = []
    class Child:
        returncode = None
        def __init__(self, command, **kwargs):
            self.command = command
            self.signals = []
            spawned.append(self)
        def poll(self):
            return self.returncode
        def send_signal(self, value):
            self.signals.append(value)
        def wait(self, timeout):
            self.returncode = 0
    monkeypatch.setattr(interactive.os, "geteuid", lambda: 0)
    monkeypatch.setattr(interactive.subprocess, "Popen", Child)
    inputs(monkeypatch, ["5"])
    interactive.native_session(directory, tmp_path / "config.json", provision=False, allow_control=False)
    assert spawned[0].signals == [signal.SIGTERM]
    assert "--allow-control" not in spawned[0].command
    assert "--provision" not in spawned[0].command
    assert next(directory.glob("interactive-*.log")).stat().st_mode & 0o777 == 0o600
