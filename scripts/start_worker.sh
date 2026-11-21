#!/bin/sh
set -eu
python manage.py run_tick --catchup
exec python manage.py qcluster
