from __future__ import annotations

import json
import signal
from collections import deque
from dataclasses import dataclass, field, InitVar
from enum import IntEnum
from os import listdir
from os.path import exists
from statistics import mean
from threading import Thread
from time import sleep
from typing import Any, TextIO

from imath import clamp, lerp

PATH_AUTO_RESOLVE_SYMBOL: str = "::"


def get(d: dict, *keys: str) -> str | int | dict | None:
	try:
		for key in keys:
			d = d[key]
		return d
	except KeyError:
		return None


def _resolve_path(path: str) -> str:
	if PATH_AUTO_RESOLVE_SYMBOL not in path:
		print(
			f"path '{path}' is missing auto resolve symbol ('{PATH_AUTO_RESOLVE_SYMBOL}'). This means the given path "
			f"is most likely an absolute path and it probably will break on next reboot."
		)
		return path
	
	parts: list[str] = path.split(PATH_AUTO_RESOLVE_SYMBOL)
	
	return f"{parts[0]}{listdir(parts[0])[0]}{parts[1]}"


def resolve_and_validate_path(path: str, mode: str) -> TextIO | str:
	p = _resolve_path(path)
	
	if not exists(p):
		print(f"file '{p}' (from '{path}') does not exist")
		exit(1)
	
	return open(p, mode)


def clamped_linear_interpolation(minimum: int, maximum: int, percentage: float) -> int:
	return clamp(round(lerp(minimum, maximum, percentage)), minimum, maximum)


def pwm_clamp(pwm_value: int) -> int:
	return clamp(pwm_value, 0, 255)


class PWMMode(IntEnum):
	MANUAL = 1
	AUTO = 5


@dataclass
class PWMConfiguration:
	minimum: int
	maximum: int
	fan_stop: int
	fan_start: int
	
	@classmethod
	def from_json(cls, data: dict[str, Any]):
		return cls(
			pwm_clamp(data["minimum"]),
			pwm_clamp(data["maximum"]),
			pwm_clamp(data["fan_stop"]),
			pwm_clamp(data["fan_start"])
		)


@dataclass
class TemperatureConfiguration:
	input: TextIO
	minimum: int
	maximum: int
	previous_temperatures: deque[int] = field(init=False)
	average: InitVar[int] = 1  # NOSONAR
	
	def __post_init__(self, average: int):
		self.previous_temperatures = deque(maxlen=average)
	
	@classmethod
	def from_json(cls, data: dict[str, Any], average: int):
		return cls(
			resolve_and_validate_path(data["input"], "r"), data["minimum"], data["maximum"], average
		)
	
	def _read_temperature(self) -> int:
		self.input.seek(0)
		return int(int(self.input.readline()) / 1000)
	
	def get_temperature(self) -> int:
		self.previous_temperatures.append(self._read_temperature())
		return round(mean(self.previous_temperatures))
	
	@property
	def delta(self) -> int:
		return self.maximum - self.minimum


@dataclass
class FanConfiguration:
	pwm_file: TextIO
	fan_input: TextIO | None
	temperature: TemperatureConfiguration
	pwm: PWMConfiguration
	mode_file: TextIO = field(init=False)
	is_running: bool = field(init=False, default=True)
	original_mode: int = field(init=False)
	
	def __post_init__(self):
		self.mode_file = open(f"{self.pwm_file.name}_enable", "r+")
		self.mode_file.seek(0)
		self.original_mode = int(self.mode_file.readline())
		self.mode_file.seek(0)
	
	@classmethod
	def from_json(cls, data: dict[str, Any], pwm_file: str):
		return cls(
			resolve_and_validate_path(pwm_file, "w"),
			resolve_and_validate_path(data["fan_input"], "r") if "fan_input" in data else None,
			TemperatureConfiguration.from_json(data["temperature"], data["average"]),
			PWMConfiguration.from_json(data["pwm"])
		)
	
	def set_mode(self, mode: PWMMode) -> None:
		self.mode_file.write(str(mode))
		self.mode_file.seek(0)
	
	def write_pwm(self, pwm: int) -> None:
		self.pwm_file.seek(0)
		self.pwm_file.write(str(pwm))
	
	def read_fan_input(self) -> int:
		self.fan_input.seek(0)
		return int(self.fan_input.readline())
	
	def get_temperature_percentage(self) -> float:
		return (self.temperature.get_temperature() - self.temperature.minimum) / self.temperature.delta
	
	def get_current_pwm(self) -> int:
		# lerp(pwmmin, pwmmax, (tcur - tmin) / (tmax - tmin) )
		
		return clamped_linear_interpolation(self.pwm.minimum, self.pwm.maximum, self.get_temperature_percentage())
	
	def run(self, interval: float) -> None:
		"""
		Starts to control the fan. Requires a separate thread, otherwise only this fan will be controlled.
		"""
		self.set_mode(PWMMode.MANUAL)
		while self.is_running:
			self.write_pwm(self.get_current_pwm())
			
			sleep(interval)
	
	def shutdown(self) -> None:
		# restore previous fan mode
		self.mode_file.write(str(self.original_mode))
		self.mode_file.seek(0)
		self.is_running = False


@dataclass
class Configuration:
	interval: float
	controls: list[FanConfiguration]
	
	@classmethod
	def from_json(cls, data: dict[str, Any]):
		return cls(
			data["interval"],
			[FanConfiguration.from_json(pwm_config, pwm_file) for pwm_file, pwm_config in
				data["controls"].items()]
		)


def read_configuration(path: str) -> Configuration:
	with open(path, "r") as configuration_file:
		lines: list[str] = configuration_file.readlines()
	
	no_comment_lines: list[str] = []
	
	# the worst json5 "implementation" ever
	for i in range(len(lines)):
		if lines[i].strip().startswith("//"):
			continue
		no_comment_lines.append(lines[i])
	
	configuration_data = json.loads("".join(no_comment_lines))
	return Configuration.from_json(configuration_data)


def main() -> None:
	configuration: Configuration = read_configuration("config.json5")
	
	def graceful_shutdown(signum, frame) -> None:
		print("received exit signal, shutting down...")
		for ctrl in configuration.controls:
			ctrl.shutdown()
	
	for control in configuration.controls:
		Thread(target=control.run, args=[configuration.interval]).start()
	
	# allow for graceful shutdowns
	signal.signal(signal.SIGQUIT, graceful_shutdown)
	signal.signal(signal.SIGTERM, graceful_shutdown)
	signal.signal(signal.SIGHUP, graceful_shutdown)
	signal.signal(signal.SIGINT, graceful_shutdown)


if __name__ == '__main__':
	main()
