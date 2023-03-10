#!/bin/bash
################################################
# Compile MITgcm.
################################################

# USER VARIABLE
# Path to MITgcm source code: recommend to use the one inside UaMITgcm
MIT_SOURCE=$WORK/UaMITgcm/MITgcm_67s
ROOTDIR=$MIT_SOURCE
# Path to file containing Fortran flags etc
BUILD_OPTIONS=../linux_x86_monsoon_ifort

# Empty the build directory - but first make sure it exists!
if [ -d "../build" ]; then
  cd ../build
  rm -rf *
else
  echo 'Creating build directory'
  mkdir ../build
  cd ../build
fi

# Generate a Makefile
$MIT_SOURCE/tools/genmake2 -ieee -mods=../code -of=$BUILD_OPTIONS -mpi

# Run the Makefile
make depend
make
