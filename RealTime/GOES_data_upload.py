import pandas as pd
import os
import glob
import numpy as np
import xarray as xr
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from siphon.catalog import TDSCatalog
import time

# def get_new_data(cat_url, out_dir, n_files=30, max_workers=4):
#     cat = TDSCatalog(cat_url)

#     # taking the last 30 (or other) files!
#     ds_iter = (cat.datasets[i] for i in range(-n_files, 0))

#     def download(ds):
#         url = ds.access_urls['HTTPServer']
#         fname = os.path.join(out_dir, str(ds))
#         urllib.request.urlretrieve(url, fname)
#         return fname

#     with ThreadPoolExecutor(max_workers=max_workers) as executor:
#         for _ in executor.map(download, ds_iter):
#             pass

def get_new_data(cat_url, out_dir, start_from_end=-30, max_workers=4):
    """Download the N most recent files (start_from_end is negative index)."""
    cat = TDSCatalog(cat_url)
    datasets = list(cat.datasets.values())

    # Handle out-of-range gracefully
    n_files = abs(start_from_end)
    ds_iter = datasets[start_from_end:] if len(datasets) >= n_files else datasets

    def download(ds):
        url = ds.access_urls['HTTPServer']
        fname = os.path.join(out_dir, str(ds))
        if not os.path.exists(fname):  # Cache check
            urllib.request.urlretrieve(url, fname)
        return fname

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _ in executor.map(download, ds_iter):
            pass


def make_irradiance(ifn, median=True):
    """I am putting a few of the steps together, so now I have the median (or mean) times being saved with the header I want for the dataframe, as well as just taking
    the average time right away."""
    with xr.open_dataset(ifn) as ds:
        dtime = ds['product_time'].data
        IL1 = ds['irradiance_xrsb1'].data
        IL2 = ds['irradiance_xrsb2'].data
        IS1 = ds['irradiance_xrsa1'].data
        IS2 = ds['irradiance_xrsa2'].data
        LF  = ds['primary_xrsb'].data
        SF  = ds['primary_xrsa'].data

    XRSB = IL1*(1 - LF) + IL2*LF ## this gives you an array with only the irradiance from the primary channel for each datapoint!
    XRSA = IS1*(1 - SF) + IS2*SF

    # Compute median and mid-time here instead of doing it later
    tdif = int(dtime[1] - dtime[0])
    tave = dtime[0] + np.timedelta64(tdif // 2, 'ns')

    if not median: #so we take the mean instead 
        return {
            'time_tag': np.datetime_as_string(tave),
            'xrsa': float(np.mean(XRSA)), 
            'xrsb': float(np.mean(XRSB))
        }
    return {
        'time_tag': np.datetime_as_string(tave),
        'xrsa': float(np.median(XRSA)),
        'xrsb': float(np.median(XRSB))
    } #returning a little dictionary with the values!

from concurrent.futures import ProcessPoolExecutor

def process_irradiance_all_files(ifns, max_workers=4):
    #ifns = sorted(glob.glob(os.path.join(out_dir, "OR_EXIS*.nc")))
    if not ifns:
        raise FileNotFoundError("where are the files??? did you remember to download them?")

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(make_irradiance, ifns))
    
    df = pd.DataFrame(results)
    df['time_tag'] = pd.to_datetime(df['time_tag'])
    df = df.sort_values('time_tag').reset_index(drop=True)
    df['satellite'] = [18]*len(df['xrsa'])
    return df


# def load_new_realtime_XRS(nfiles=15, median=True, max_workers=4):

#     try:
#         ### getting the goes data
#         base_cat_url = 'https://thredds-test.unidata.ucar.edu/thredds/catalog/satellite/{satellite}/{sat_pos}/{platform}/{dataset}/{product}/{date}/catalog.xml' 

#         # Desired data (need to get G18 and G19 and combine in the end)
#         satellite = 'goes'
#         sat_pos = 'east' # "east" | "west"
#         platform = 'grb'
#         dataset = 'EXIS'
#         product = 'SFXR' # "SFXR" | "SFEU"
#         date = 'current'

#         # Set data retrieval path
#         cat_url = base_cat_url.format(satellite = satellite, sat_pos=sat_pos, platform = platform, dataset = dataset, product=product,  date = date)

#         out_dir = os.getcwd()
        
#         # loading in the files
#         get_new_data(cat_url, out_dir, n_files=nfiles, max_workers=max_workers)
#         goes_current = process_irradiance_all_files(out_dir, max_workers=max_workers)
#         for file_path in glob.glob(os.path.join(out_dir, "*.nc")):
#             os.remove(file_path) #gotta get rid of all the .nc files before we try again
#         return goes_current

#     except Exception as e:
#         print(f"Likely GOES download error from `wget`:\n{e}")
#         return load_new_realtime_XRS()
    
def load_new_realtime_XRS(nfiles=5, median=True, max_workers=4, initial=False):
    """
    initial=True → do large download (~65 files)
    otherwise → small incremental update (~5 files)
    """
    try:
        base_cat_url = (
            "https://thredds-test.unidata.ucar.edu/thredds/catalog/"
            "satellite/{satellite}/{sat_pos}/{platform}/{dataset}/{product}/{date}/catalog.xml"
        )

        cat_url = base_cat_url.format(
            satellite='goes', sat_pos='east', platform='grb',
            dataset='EXIS', product='SFXR', date='current'
        )
        out_dir = os.path.join(os.getcwd(), "goes_cache")
        os.makedirs(out_dir, exist_ok=True)

        if initial:
            nfiles = 65  # load ~30–35 minutes of history
        else:
            nfiles = nfiles  # refresh 4–5 most recent

        # Download new or missing files
        get_new_data(cat_url, out_dir, start_from_end=-nfiles, max_workers=max_workers)

        # Select which files to process
        all_files = sorted(glob.glob(os.path.join(out_dir, "*.nc")))

        if not all_files:
            raise FileNotFoundError("No GOES .nc files found in cache.")

        # I only want to give the last 5 files if not the inital download to goes_current
        if initial:
            files_to_process = all_files
        else:
            files_to_process = all_files[-nfiles:]

        goes_current = process_irradiance_all_files(files_to_process, max_workers=max_workers)

        # Only hold onto the last ~2 hours of data
        if len(all_files) > 240:
            oldest = all_files[:-240]
            for f in oldest:
                os.remove(f)

        return goes_current

    except Exception as e:
        print(f"GOES download error:\n{e}")
        time.sleep(5)
        return load_new_realtime_XRS(nfiles=nfiles, median=median, max_workers=max_workers)
