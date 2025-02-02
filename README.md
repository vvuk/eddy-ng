# eddy-ng

This is a standalone `eddy-ng` repository, intended to be integrated into your own Klipper installation.

## Installation

1. Clone this repository:

```
cd ~
git clone https://github.com/vvuk/eddy-ng
```

2. Run the install script:

```
cd ~/eddy-ng
./install.sh
```

(If your klipper isn't installed in `~/klipper`, provide the path as the first argument, i.e. `./install.sh ~/my-klipper`.)

3. Follow the rest of the full `eddy-ng` setup instructions that are [available in the wiki](https://github.com/vvuk/eddy-ng/wiki).

## Updating

Run a `git pull` and then run `./install.sh` again:

```
cd ~/eddy-ng
git pull
./install.sh
```
