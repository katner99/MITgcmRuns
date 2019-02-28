################################################################################
# Functions to exchange data between MITgcm and Ua, and adjust the MITgcm geometry/initial conditions as needed.
################################################################################

import numpy as np
from scipy.io import savemat, loadmat
import os
import shutil

from coupling_utils import read_last_output, move_to_dir, copy_to_dir, find_dump_prefixes, move_processed_files, make_tmp_copy

from mitgcm_python.utils import convert_ismr, calc_hfac
from mitgcm_python.make_domain import do_digging, do_zapping
from mitgcm_python.file_io import read_binary, write_binary, set_dtype
from mitgcm_python.interpolation import discard_and_fill
from mitgcm_python.ics_obcs import calc_load_anomaly


# Create dummy initial conditions files for the files which might not exist
# because their variables initialise with all zeros.
# Only called if options.restart_type='zero'.
def zero_ini_files (options):

    # Set data type string to read from binary
    dtype = set_dtype(options.readBinaryPrec, 'big')

    # Inner function to create a file if it doesn't exist, with data of the same size as the given array
    def check_create_zero_file (fname, base_array):
        file_path = options.mit_run_dir+fname
        if not os.path.isfile(file_path):
            data = base_array*0
            write_binary(data, file_path, prec=options.readBinaryPrec)
    
    # Start with 3D ocean variables (u and v)
    # Read the initial temperature file so we have the right size
    temp = np.fromfile(options.mit_run_dir+options.ini_temp_file, dtype=dtype)
    check_create_zero_file(options.ini_u_file, temp)
    check_create_zero_file(options.ini_v_file, temp)

    if options.use_seaice:
        # 2D Sea ice variables (area, heff, hsnow, uice, vice)
        # Read the initial pload file so we have the right size
        pload = np.fromfile(options.mit_run_dir+options.pload_file, dtype=dtype)
        check_create_zero_file(options.ini_area_file, pload)
        check_create_zero_file(options.ini_heff_file, pload)
        check_create_zero_file(options.ini_hsnow_file, pload)
        check_create_zero_file(options.ini_uice_file, pload)
        check_create_zero_file(options.ini_vice_file, pload)    
    

# Copy the XC and YC grid files from one directory to another.
# In practice, they will be copied from the MITgcm run directory to the central output directory, so that Ua can read them.
def copy_grid (mit_dir, out_dir):
    copy_to_dir('XC.data', mit_dir, out_dir)
    copy_to_dir('XC.meta', mit_dir, out_dir)
    copy_to_dir('YC.data', mit_dir, out_dir)
    copy_to_dir('YC.meta', mit_dir, out_dir)
        

# Put MITgcm melt rates in the right format for Ua. No need to interpolate.

# Arguments:
# mit_dir: MITgcm directory containing SHIfwFlx output
# ua_out_file: desired path to .mat file for Ua to read melt rates from.
# grid: Grid object (for the MITgcm segment that just finished)
# options: Options object

def extract_melt_rates (mit_dir, ua_out_file, grid, options):

    # Read the most recent ice shelf melt rate output and convert to m/y,
    # melting is negative as per Ua convention.
    # Make sure it's from the last timestep of the previous simulation.
    ismr = -1*convert_ismr(read_last_output(mit_dir, options.ismr_name, 'SHIfwFlx', timestep=options.last_timestep))

    if os.path.isfile(ua_out_file):
        # Make a backup copy of the old file
        make_tmp_copy(ua_out_file)

    # Write to Matlab file for Ua, as a long 1D array
    print 'Writing ' + ua_out_file
    savemat(ua_out_file, {'meltrate':ismr.ravel()})


# Given the updated ice shelf draft from Ua, adjust the draft and/or bathymetry so that MITgcm is happy. In order to have fully connected adjacent water columns, they must overlap by at least two wet cells.
# There are three ways to do this (set in options.digging):
#    'none': ignore the 2-cell rule and don't dig anything
#    'bathy': dig bathymetry which is too shallow (starting with original, undug bathymetry so that digging is reversible)
#    'draft': dig ice shelf drafts which are too deep
# In all cases, also remove ice shelf drafts which are too thin.
# Ua does not see these changes to the geometry.

# Arguments:
# ua_draft_file: path to .mat ice shelf draft file written by Ua at the end of the last segment
# mit_dir: path to MITgcm directory containing binary files for bathymetry and ice shelf draft
# grid: Grid object
# options: Options object

def adjust_mit_geom (ua_draft_file, mit_dir, grid, options):

    # Read the bathymetry, ice shelf draft and mask from Ua
    f = loadmat(ua_draft_file)
    bathy = np.transpose(f['B_forMITgcm'])
    draft = np.transpose(f['b_forMITgcm'])
    mask = np.transpose(f['mask_forMITgcm'])
    # Mask grounded ice out of both fields
    bathy[mask==0] = 0
    draft[mask==0] = 0

    if options.misomip_wall:
        print 'Building walls in MISOMIP domain'
        bathy[0,:] = 0
        draft[0,:] = 0
        bathy[-1,:] = 0
        draft[-1,:] = 0

    if options.digging == 'none':
        print 'Not doing digging as per user request'
    elif options.digging == 'bathy':
        print 'Digging bathymetry which is too shallow'
        bathy = do_digging(bathy, draft, grid.dz, grid.z_edges, hFacMin=options.hFacMin, hFacMinDr=options.hFacMinDr, dig_option='bathy')
    elif options.digging == 'draft':
        print 'Digging ice shelf drafts which are too deep'
        draft = do_digging(bathy, draft, grid.dz, grid.z_edges, hFacMin=options.hFacMin, hFacMinDr=options.hFacMinDr, dig_option='draft')

    print 'Zapping ice shelf drafts which are too thin'
    draft = do_zapping(draft, draft!=0, grid.dz, grid.z_edges, hFacMinDr=options.hFacMinDr)[0]

    # Make a copy of the original bathymetry and ice shelf draft
    make_tmp_copy(mit_dir+options.draftFile)
    make_tmp_copy(mit_dir+options.bathyFile)
    
    # Write to file
    write_binary(draft, mit_dir+options.draftFile, prec=options.readBinaryPrec)
    write_binary(bathy, mit_dir+options.bathyFile, prec=options.readBinaryPrec)


# Figure out which cells have newly opened since the last segment. Helper function called by set_mit_ics and adjust_mit_pickup.
def find_newly_open_cells (mit_dir, options, grid):

    # Read the new ice shelf draft, and also the bathymetry
    draft = read_binary(mit_dir+options.draftFile, [grid.nx, grid.ny], 'xy', prec=options.readBinaryPrec)
    bathy = read_binary(mit_dir+options.bathyFile, [grid.nx, grid.ny], 'xy', prec=options.readBinaryPrec)
    # Calculate the new hFacC
    hfac_new = calc_hfac(bathy, draft, grid.z_edges, hFacMin=options.hFacMin, hFacMinDr=options.hFacMinDr)
    # Find all the cells which are newly open
    newly_open = (hfac_new!=0)*(grid.hfac==0)
    # Also get the new 3D mask
    mask_new = (hfac_new!=0).astype(int)
    return newly_open, hfac_new, mask_new


# Extrapolate a field into its newly opened cells, and mask the closed cells with zeros. Helper function called by set_mit_ics and adjust_mit_pickup. Can be 3D (default) or 2D.
def extrapolate_into_new (data, newly_open, mask_new, dims=3):

    if dims not in [2, 3]:
        print 'Error (extrapolate_into_new): Must be either a 2D or 3D field.'
        sys.exit()
    use_3d = dims==3
    return discard_and_fill(data, [], newly_open, missing_val=0, use_3d=use_3d, preference='vertical')*mask_new


# Read MITgcm's state variables from the end of the last segment, and adjust them to create initial conditions for the next segment. Only called if options.restart_type='zero'.
# Any cells which have opened up since the last segment (due to Ua's simulated ice shelf draft changes + MITgcm's adjustments eg digging) will have temperature and salinity set to the average of their nearest neighbours, and velocity to zero.
# Sea ice (if active) will also set to zero area, thickness, snow depth, and velocity in the event of a retreat of the ice shelf front.
# Also set the new pressure load anomaly.

# Arguments:
# mit_dir: path to MITgcm directory containing binary files for bathymetry, ice shelf draft, initial conditions, and dump of final state variables
# grid: Grid object
# options: Options object

def set_mit_ics (mit_dir, grid, options):

    # Inner function to read the final dump of a given variable
    def read_last_dump (var_name):
        return read_last_output(mit_dir, var_name, None, timestep=options.last_timestep)
    
    # Read the final ocean state variables
    temp = read_last_dump('T')
    salt = read_last_dump('S')
    u = read_last_dump('U')
    v = read_last_dump('V')
    if options.use_seaice:
        # Read the final sea ice state variables
        aice = read_last_dump('AREA')
        hice = read_last_dump('HEFF')
        hsnow = read_last_dump('HSNOW')
        uice = read_last_dump('UICE')
        vice = read_last_dump('VICE')

    print 'Selecting newly opened cells'
    newly_open, hfac_new, mask_new = find_newly_open_cells(mit_dir, options,  grid)

    print 'Extrapolating temperature into newly opened cells'
    temp_new = extrapolate_into_new(temp, newly_open, mask_new)
    print 'Extrapolating salinity into newly opened cells'
    salt_new = extrapolate_into_new(salt, newly_open, mask_new)

    # Make backup copies of old initial conditions files before we overwrite them
    files_to_copy = [options.ini_temp_file, options.ini_salt_file, options.ini_u_file, options.ini_v_file, options.pload_file]
    if options.use_seaice:
        files_to_copy += [options.ini_area_file, options.ini_heff_file, options.ini_hsnow_file, options.ini_uice_file, options.ini_vice_file]
    for fname in files_to_copy:
        make_tmp_copy(mit_dir+fname)
    
    # Write the new initial conditions
    write_binary(temp_new, mit_dir+options.ini_temp_file, prec=options.readBinaryPrec)
    write_binary(salt_new, mit_dir+options.ini_salt_file, prec=options.readBinaryPrec)

    # Write the initial conditions which haven't changed
    # No need to mask them, as velocity and sea ice variables are always masked when they're read in
    write_binary(u, mit_dir+options.ini_u_file, prec=options.readBinaryPrec)
    write_binary(v, mit_dir+options.ini_v_file, prec=options.readBinaryPrec)
    if options.use_seaice:
        write_binary(aice, mit_dir+options.ini_area_file, prec=options.readBinaryPrec)
        write_binary(hice, mit_dir+options.ini_heff_file, prec=options.readBinaryPrec)
        write_binary(hsnow, mit_dir+options.ini_hsnow_file, prec=options.readBinaryPrec)
        write_binary(uice, mit_dir+options.ini_uice_file, prec=options.readBinaryPrec)
        write_binary(vice, mit_dir+options.ini_vice_file, prec=options.readBinaryPrec)

    print 'Calculating pressure load anomaly'
    calc_load_anomaly(grid, mit_dir+options.pload_file, option=options.pload_option, ini_temp=temp_new, ini_salt=salt_new, constant_t=options.pload_temp, constant_s=options.pload_salt, eosType=options.eosType, rhoConst=options.rhoConst, tAlpha=options.tAlpha, sBeta=options.sBeta, Tref=options.Tref, Sref=options.Sref, hfac=hfac_new, prec=options.readBinaryPrec)


def adjust_mit_pickup (mit_dir, grid, options):

    print 'Reading last pickup file'
    temp, salt, phihyd, etan, etah, u, v, gunm1, gvnm1, detahdt = read_last_output(mit_dir, 'pickup', ['Theta', 'Salt', 'PhiHyd', 'EtaN', 'EtaH', 'Uvel', 'Vvel', 'GuNm1', 'GvNm1', 'dEtaHdt'], timestep=options.last_timestep, nz=grid.nz)

    # Make sure there are no ptracers in this simulation
    for fname in os.listdir(mit_dir):
        if fname.startswith('pickup_ptracers'):
            print 'Error (adjust_mit_pickup): this run uses the ptracers package. You will need to customise the code to decide what you want to do with the ptracers at each restart.'
            sys.exit()

            
    # Process each variable (need at least two auxiliary functions for this)
    # Adjust Uvel, Vvel to preserve transport (make this an option which can also be called by set_mit_ics)
    # Rewrite pickup file - how to do this in the right order? Need file name and variable order, can we get this from read_last_output?
    # Recalculate pload as before (need to edit calc_load_anomaly so it can just accept T/S fields instead of needing to read from files)


# Convert all the MITgcm binary output files in run/ to NetCDF, using the xmitgcm package.
# Arguments:
# options: Options object
def convert_mit_output (options):

    # Wrap import statement inside this function, so that xmitgcm isn't required to be installed unless needed
    from xmitgcm import open_mdsdataset

    # Get startDate in the right format for NetCDF
    ref_date = options.last_start_date[:4]+'-'+options.last_start_date[4:6]+'-01 0:0:0'

    # Make a temporary directory to save already-processed files
    tmp_dir = options.mit_run_dir + 'tmp_mds/'
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)

    # Inner function to read MDS files and convert to NetCDF.
    # If dump=True, only read dump files, and only from the given timestep.
    # Then move the original files to a temporary directory so they're hidden
    # at the end.
    # If dump=False, read whatever files are left.
    def convert_files (nc_name, dump=True, tstep=None):
        if dump:
            if tstep is None:
                print 'Error (convert_files): must define tstep'
                sys.exit()
            iters = [tstep]
            prefixes = find_dump_prefixes(options.mit_run_dir, tstep)
        else:
            iters = 'all'
            prefixes = None
        # Read all the files matching the criteria
        ds = open_mdsdataset(options.mit_run_dir, iters=iters, prefix=prefixes, delta_t=options.deltaT, ref_date=ref_date)
        # Save to NetCDF file
        ds.to_netcdf(options.mit_run_dir+nc_name, unlimited_dims=['time'])
        if dump:
            # Move to temporary directory
            move_processed_files(options.mit_run_dir, tmp_dir, prefixes, tstep)

    # Process first dump
    convert_files(options.dump_start_nc_name, tstep=0)
    # Process last dump
    convert_files(options.dump_end_nc_name, tstep=options.last_timestep)
    # Process diagnostics, i.e. everything that's left
    convert_files(options.mit_nc_name, dump=False)

    # Move everything back out of the temporary directory
    for fname in os.listdir(tmp_dir):
        move_to_dir(fname, tmp_dir, options.mit_run_dir)
    # Make sure we can safely delete it
    if len(os.listdir(tmp_dir)) != 0:
        print 'Error (convert_mit_output): '+tmp_dir+' is not empty'
        sys.exit()
    os.rmdir(tmp_dir)


# Gather all output from MITgcm and Ua, moving it to a common subdirectory of options.output_dir.
# Arguments:
# options: Options object
# spinup: boolean indicating we're in the ocean-only spinup phase, so there is no Ua output to deal with
# first_coupled: boolean indicating we're about to start the first coupled timestep, so there is still no Ua output to deal with
def gather_output (options, spinup, first_coupled):

    # Make a subdirectory named after the starting date of the simulation segment
    new_dir = options.output_dir + options.last_start_date + '/'
    print 'Creating ' + new_dir
    os.mkdir(new_dir)

    # Make a subdirectory for MITgcm
    new_mit_dir = new_dir + 'MITgcm/'
    os.mkdir(new_mit_dir)

    # Inner function to check a file exists, and if so, move it to the new MITgcm output folder. This is just called for xmitgcm output files.
    def check_and_move (directory, fname):
        if not os.path.isfile(directory+fname):
            print 'Error gathering output'
            print file_path + ' does not exist'
            sys.exit()
        move_to_dir(fname, directory, new_mit_dir)

    if options.use_xmitgcm:
        # Move the NetCDF files created by convert_mit_output into the new folder
        check_and_move(options.mit_run_dir, options.dump_start_nc_name)
        check_and_move(options.mit_run_dir, options.dump_end_nc_name)
        check_and_move(options.mit_run_dir, options.mit_nc_name)
            
    # Deal with MITgcm binary output files
    for fname in os.listdir(options.mit_run_dir):
        if fname.endswith('.data') or fname.endswith('.meta'):
            if fname.startswith('pickup'):
                # Save pickup files
                move_to_dir(fname, options.mit_run_dir, new_mit_dir)
            else:
                if options.use_xmitgcm:
                    # Delete binary files which were savely converted to NetCDF
                    os.remove(options.mit_run_dir+fname)
                else:
                    # Move binary files to output directory
                    move_to_dir(fname, options.mit_run_dir, new_mit_dir)

    # Inner function to copy topography/ICs/pload files which are modified every coupling timestep, and for which temporary copies are made prior to modification.
    def copy_tmp_file (fname, source_dir, target_dir):
        # First check if it was modified this timestep
        if os.path.isfile(source_dir+fname+'.tmp'):
            # Move the temporary copies
            os.rename(source_dir+fname+'.tmp', target_dir+fname)
        else:
            # They were not modified, so copy them
            copy_to_dir(fname, source_dir, target_dir)

    # List of such files to copy from MITgcm run directory
    mit_file_names = [options.draftFile, options.bathyFile, options.ini_temp_file, options.ini_salt_file, options.ini_u_file, options.ini_v_file, options.pload_file]
    if options.use_seaice:
        mit_file_names += [options.ini_area_file, options.ini_heff_file, options.ini_hsnow_file, options.ini_uice_file, options.ini_vice_file]
    # Now copy them
    for fname in mit_file_names:
        copy_tmp_file(fname, options.mit_run_dir, new_mit_dir)
    
    if not spinup and not first_coupled:
        # Make a subdirectory for Ua
        new_ua_dir = new_dir + 'Ua/'
        os.mkdir(new_ua_dir)
        # Move Ua output into this folder
        for fname in os.listdir(options.ua_output_dir):
            move_to_dir(fname, options.ua_output_dir, new_ua_dir)
        # Now copy the restart file from the main Ua directory
        for fname in os.listdir(options.ua_exe_dir):
            if fname.endswith('RestartFile.mat'):
                copy_to_dir(fname, options.ua_exe_dir, new_ua_dir)
        # Also copy melt file from MITgcm
        copy_tmp_file(options.ua_melt_file, options.output_dir, new_ua_dir)
        # Make sure the draft file exists
        if not os.path.isfile(new_ua_dir+options.ua_draft_file):
            print 'Error gathering output'
            print 'Ua did not create the draft file '+ua_draft_file
            sys.exit()
            
        
    


    
    

    
