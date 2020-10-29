#!/bin/bash

if [ $# -ne 1 ]; then
    echo "Missing Captured dir argument"
    exit 1
fi

captured_dir="${1}"

cd ${captured_dir}

       camera_name=$(basename $(pwd))
       camera_name=${camera_name%%_*}
        ftp_server="ftp://ftp.astronomy.org.il"
   ftp_credentials="camera@astronomy.org.il:M31Andromeda"
      ftp_location=${ftp_server}/IMN/radiants/${camera_name}
radiants_text_file=$(find . -name '*_radiants.txt')
radiants_text_file=${radiants_text_file#./}

if [ -s "${radiants_text_file}" ]; then
    echo "Uploading ${radiants_text_file} to ${ftp_location} ..."
    curl --silent --user "${ftp_credentials}" --upload-file ${radiants_text_file} ${ftp_location}/${radiants_text_file} --ftp-create-dirs
    logger -t IMN "Uploaded ${radiants_text_file} to ${ftp_location}/${radiants_text_file}"
fi
