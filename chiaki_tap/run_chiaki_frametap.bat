@echo off
rem Start chiaki-ng with the frame tap on, so Keel's "run.py --chiaki" receives every video frame.
rem Without CHIAKI_FRAMETAP=1 this build behaves exactly like stock chiaki-ng.
set CHIAKI_FRAMETAP=1
start "" "%~dp0chiaki.exe" %*
