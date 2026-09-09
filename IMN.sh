#!/bin/bash

if [ $# -ne 2 ]; then
    echo "Usage: $(basename "$0") <captured_night_dir> <archived_night_dir>"
    exit 1
fi

captured_dir="${1}"
archived_dir="${2}"

cd "${captured_dir}" || { echo "Cannot cd to ${captured_dir}"; exit 1; }

       camera_name=$(basename $(pwd))
       camera_name=${camera_name%%_*}
        ftp_server="ftp://ftp.astronomy.org.il"
      ftp_location=${ftp_server}/IMN/radiants/${camera_name}
   ftp_credentials="camera@astronomy.org.il:M31Andromeda"
radiants_text_file=$(find . -name '*_radiants.txt')
radiants_text_file=${radiants_text_file#./}

if [ -s "${radiants_text_file}" ]; then
    echo "Uploading ${radiants_text_file} to ${ftp_location} ..."
    curl --silent --user "${ftp_credentials}" --upload-file ${radiants_text_file} ${ftp_location}/${radiants_text_file} --ftp-create-dirs
    logger -t IMN "Uploaded ${radiants_text_file} to ${ftp_location}/${radiants_text_file}"
else
    logger -t IMN "No radiants file in ${captured_dir}"
fi
