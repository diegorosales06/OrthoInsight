@echo off

echo Deleting old OrthoInsight directory...

ssh sparkrnd@raspberrypi.local "rm -rf /home/sparkrnd/OrthoInsight"

echo Uploading new OrthoInsight directory...

scp -r "C:\Users\5035977\Downloads\OrthoInsight" sparkrnd@raspberrypi.local:/home/sparkrnd/

pause