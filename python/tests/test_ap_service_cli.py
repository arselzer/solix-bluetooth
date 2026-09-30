"""AP-service public command names and options require no device transport."""

import pytest

from solix_link.cli import parser


@pytest.mark.parametrize("command,extra", [
    ("ap-service-init", ["--name", "ups", "--serial-file", "/tmp/serial",
                         "--interface", "wlan_unused", "--phy", "phy9", "--country", "AT"]),
    ("ap-service-run", []),
    ("ap-service-status", []),
    ("ap-service-readiness", []),
    ("ap-service-set-charge-power", ["--watts", "800"]),
    ("ap-service-set-charge-cap", ["--upper", "90"]),
    ("ap-service-set-reserve", ["--reserve", "85"]),
    ("ap-service-set-tou", ["--mode", "standard"]),
    ("ap-service-grid", ["--timeout", "20"]),
    ("ap-service-serve", []),
])
def test_ap_service_names_are_the_only_public_command_names(command, extra):
    command_parser = parser()
    args = command_parser.parse_args([command, "--directory", "/tmp/private-ap", *extra])
    assert args.command == command
    with pytest.raises(SystemExit):
        command_parser.parse_args([command.replace("ap-service-", "lab-"),
                                   "--directory", "/tmp/private-ap", *extra])


@pytest.mark.parametrize("command", ["interactive", "tui"])
def test_ap_service_directory_option_has_no_old_alias(command):
    command_parser = parser()
    args = command_parser.parse_args([command, "--ap-service-directory", "/tmp/private-ap"])
    assert str(args.ap_service_directory) == "/tmp/private-ap"
    with pytest.raises(SystemExit):
        command_parser.parse_args([command, "--lab-directory", "/tmp/private-ap"])


def test_energy_report_request_is_optional_and_explicit():
    command_parser = parser()
    assert not command_parser.parse_args(["ap-service-run", "--directory", "/tmp/private-ap"]).energy_reports
    assert command_parser.parse_args(["ap-service-run", "--directory", "/tmp/private-ap", "--energy-reports"]).energy_reports
