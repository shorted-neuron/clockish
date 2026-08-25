clockish standard SD card image 20260622
========================================

contains SSID and secret!

- started from standard RaspiOS Trixie, lite, 32 bit, for pi1 (arm6l so its compatible with entire line?)
- After using rpi-imager to write SD card, created partitions 3 and 4 
- partition 3 will then be removed to make empty space to extend rootfs.  
- partition 4 can be swap or re-allocated later.


### boot and configure

After image and initial boot, did these things:

1. raspi-config
  - set WiFi SSID and secret if not done already
  - set WiFi power saving to OFF
  - enable SPI and i2c interfaces
  - set locale and keyboard properly (why didnt raspi-imager do this?)
  - reboot due to locale

2. `sudo apt update && sudo apt upgrade`

3. `sudo apt install git libopenblas0`

4. optional, recommended if configuring on very low RAM like pi1-b: 
  `sudo mkswap --verbose /dev/mmcblk0p4 && swapon /dev/mmcblk0p4`

5. `git clone https://github.com/shorted-neuron/clockish`

6. `cd clockish ; bash install.sh --verbose`

7. added independent one-shot service regenerate-sshd-keys.service

8. removed ssh host keys and machine ids 
  `bash /root/image-prep.sh `

### improvements 20260803
- added 1.2GB swap on partition3, see fstab
- `sudo mount -o remount,size=1G /tmp`
- added sudoers
- updated clockish to current 26.7.8
- all system patches/packages
- added authorized keys from devoops, rpi-desktop
- `sudo loginctl enable-linger`
- added udev rule to help with cursor blink
```
  sudo vi /etc/udev/rules.d/99-tty0-perms.rules
  KERNEL=="tty0", GROUP="video", MODE="0660"
```

### improvements 20260825
- added i2c-tools
- tested with little ssd1306 oled i2c displays
- updated clockish to current 26.8.2
- all system patches/packages
