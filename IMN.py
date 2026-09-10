#!/usr/bin/python

import os
import getpass
import logging
import datetime
import subprocess
from RMS.CaptureDuration import captureDuration

import bolides

log = logging.getLogger("IMN")

# Get the current user
user = getpass.getuser()


def run_script(argv):
	"""Run a helper script, tolerating its absence.

	The old code used os.system(), where a missing script is just a shell exit
	status of 127. subprocess.call() raises FileNotFoundError instead, which
	would abort the whole nightly hook -- and iStream is genuinely absent from
	some stations. Report it and carry on.
	"""

	if not os.path.isfile(argv[0]):
		log.warning("%s does not exist; skipping", argv[0])
		return

	try:
		status = subprocess.call(argv)
		if status != 0:
			log.error("%s exited with status %d", argv[0], status)
	except Exception as e:
		log.error("%s failed: %r", argv[0], e)


def rmsExternal(captured_night_dir, archived_night_dir, config):

	# Compute the capture duration from now
	start_time, duration = captureDuration(config.latitude, config.longitude, config.elevation)

	timenow = datetime.datetime.utcnow()
	remaining_seconds = 0

	# Compute how long to wait before capture
	if start_time != True:
		waitingtime = start_time - timenow
		remaining_seconds = int(waitingtime.total_seconds())

	lock_file = os.path.join(config.data_dir, config.reboot_lock_file)
	open(lock_file, "a").close()
	os.utime(lock_file, None)

	# Everything below runs under try/finally: RMS only ever WAITS on the reboot
	# lock (StartCapture.py) and never clears it, so a lock left behind by a
	# crash here stops the station rebooting for good.
	try:

		# Run the IMN shell script
		script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "IMN.sh")
		run_script([script_path, "{:s}".format(captured_night_dir), "{:s}".format(archived_night_dir)])

		# Run the iStream shell script (iStream lives under the RMS root; locate it
		# via config.rms_root_dir so this works whether IMN is under RMS or a sibling)
		script_path = os.path.join(config.rms_root_dir, "iStream", "iStream.sh")
		run_script([script_path, "{:s}".format(config.stationID), \
			"{:s}".format(captured_night_dir), "{:s}".format(archived_night_dir), \
			"{:.6f}".format(config.latitude), "{:.6f}".format(config.longitude), \
			"{:.1f}".format(config.elevation), "{:d}".format(config.width), \
			"{:d}".format(config.height), "{:d}".format(remaining_seconds)])

		# Select and publish the night's best bolides. Guarded so that any failure
		# (bad data, network, missing YouTube credentials) cannot break the RMS
		# nightly flow.
		try:
			bolides.publish_bolides(archived_night_dir, config)
		except Exception as e:
			log.error("bolide publishing failed: %r", e)

	finally:
		if os.path.exists(lock_file):
			os.remove(lock_file)
