#!/usr/bin/python

import os
import getpass
import datetime
import subprocess
from RMS.CaptureDuration import captureDuration

# Get the current user
user = getpass.getuser()


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

	# Run the IMN shell script
	script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "IMN.sh")
	subprocess.call([script_path, "{:s}".format(captured_night_dir), "{:s}".format(archived_night_dir)])

	# Run the iStream shell script (iStream lives under the RMS root; locate it
	# via config.rms_root_dir so this works whether IMN is under RMS or a sibling)
	script_path = os.path.join(config.rms_root_dir, "iStream", "iStream.sh")
	subprocess.call([script_path, "{:s}".format(config.stationID), \
		"{:s}".format(captured_night_dir), "{:s}".format(archived_night_dir), \
		"{:.6f}".format(config.latitude), "{:.6f}".format(config.longitude), \
		"{:.1f}".format(config.elevation), "{:d}".format(config.width), \
		"{:d}".format(config.height), "{:d}".format(remaining_seconds)])

	if os.path.exists(lock_file):
		os.remove(lock_file)
