USAGE = """

  python ngfs_metrics.py

  Run this from the model run dir.
  Processes model outputs and creates a single csv with scenario metrics, called metrics\scenario_metrics.csv
  
  This file will have 3 columns:
    1) scenario ID
    2) metric description
    3) metric value
    
  Metrics are:
    1) Affordable 1: Transportation costs as a share of household income
    2) Affordable 2: Ratio of value of auto travel time savings to incremental toll costs
    3) Efficient 1: Ratio of travel time by transit vs. auto between  representative origin-destination pairs
    4) Efficient 2: Transit, telecommute, walk and bike mode share of commute tours
    5) Reliable 1: Change in peak hour travel time on key freeway corridors and parallel arterials
    6) Reliable 2: Ratio of travel time during peak hours vs. non-peak hours between representative origin-destination pairs 
    7) Reparative 1: Absolute dollar amount of new revenues generated that is reinvested in freeway adjacent communities
    8) Reparative 2: Ratio of new revenues paid for by low-income populations to revenues reinvested toward low-income populations
    9) Safe 1: Annual number of estimated fatalities on freeways and non-freeway facilities (Annual Incidents, per 1,000,000 VMT)
    10) Safe 2: Change in vehicle miles travelled on freeway and adjacent non-freeway facilities

"""

import datetime, os, sys
import numpy, pandas as pd
import simpledbf
from collections import OrderedDict, defaultdict
import argparse
import logging
import math
import csv


# paths
TM1_GIT_DIR             = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
NGFS_MODEL_RUNS_FILE    = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "ModelRuns.xlsx")
NGFS_TOLLCLASS_FILE     = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "TOLLCLASS_Designations.xlsx")
NGFS_SCENARIOS          = "L:\\Application\\Model_One\\NextGenFwys\\Scenarios"

# These calculations are complex enough that a debug log file would be helpful to track what's happening
LOG_FILE                = "ngfs_metrics.log" # in the cwd
LOGGER                  = None # will initialize in main     

# maps TAZs to a few selected cities for Origin/Destination analysis
NGFS_OD_CITIES_FILE    = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "metrics", "Input Files", "taz_with_cities.csv")
NGFS_OD_CITIES_DF      = pd.read_csv(NGFS_OD_CITIES_FILE)

# EPC lookup file - indicates whether a TAZ is designated as an EPC in PBA2050
NGFS_EPC_TAZ_FILE    = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "metrics", "Input Files", "taz_epc_crosswalk.csv")
NGFS_EPC_TAZ_DF      = pd.read_csv(NGFS_EPC_TAZ_FILE)

# tollclass designations
TOLLCLASS_LOOKUP_DF     = pd.read_excel(NGFS_TOLLCLASS_FILE, sheet_name='Inputs_for_tollcalib', usecols=['project','facility_name','tollclass','s2toll_mandatory','THRESHOLD_SPEED','MAX_TOLL','MIN_TOLL','Grouping major','Grouping minor'])

# parallel arterials
# TODO: Document how these were made?
NGFS_PARALLEL_ARTERIALS_FILE = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "metrics", "Input Files", "ParallelArterialLinks.csv")
NGFS_PARALLEL_ARTERIALS_DF   = pd.read_csv(NGFS_PARALLEL_ARTERIALS_FILE)

# tolled arterials
# TODO: apply the same fix that LZ will implement for a_b_with_minor_groupings
NGFS_PATHWAY2_TOLLED_ARTERIALS_FILE = pd.read_csv('L:\\Application\\Model_One\\NextGenFwys\\Scenarios\\2035_TM152_NGF_NP09_Path2a_04\\OUTPUT\\avgload5period.csv').rename(columns=lambda x: x.strip())[['a','b','tollclass']]

# define origin destination pairs
NGFS_OD_CITIES_OF_INTEREST = [
    ['OAKLAND',   'SAN FRANCISCO'],
    ['VALLEJO',   'SAN FRANCISCO'],
    ['ANTIOCH',   'SAN FRANCISCO'],
    ['ANTIOCH',   'OAKLAND'],
    ['SAN JOSE',  'SAN FRANCISCO'],
    ['OAKLAND',   'PALO ALTO'],
    ['OAKLAND',   'SAN JOSE'],
    ['LIVERMORE', 'SAN JOSE'],
    ['FAIRFIELD', 'DUBLIN'],
    ['SANTA ROSA','SAN FRANCISCO']
]
NGFS_OD_CITIES_OF_INTEREST_DF = pd.DataFrame(
    data=NGFS_OD_CITIES_OF_INTEREST,
    columns=['orig_CITY', 'dest_CITY']
)
# print(NGFS_OD_CITIES_OF_INTEREST_DF)
# TODO: merge formatting and consolidate variables
# source: https://github.com/BayAreaMetro/modeling-website/wiki/InflationAssumptions
INFLATION_FACTOR = 1.03
INFLATION_00_23 = (327.06 / 180.20) * INFLATION_FACTOR
INFLATION_00_20 = 300.08 / 180.20
INFLATION_00_18 = 285.55 / 180.20
INFLATION_18_20 = 300.08 / 285.55
REVENUE_DAYS_PER_YEAR = 260

# Average Annual Costs of Driving a Car in 2020$
# Source: AAA Driving Costs 2020; mid-size sedan
# \Box\NextGen Freeways Study\04 Engagement\02_Stakeholder Engagement\Advisory Group\Meeting 02 - Apr 2022 Existing Conditions\NGFS_Advisory Group Meeting 2_Apr2022.pptx
AUTO_OWNERSHIP_COST_2020D           = 3400
AUTO_MAINTENANCE_COST_2020D         = 1430 # use a model output instead
AUTO_INSURANCE_COST_2020D           = 1250
AUTO_FINANCE_COST_2020D             = 680
AUTO_REGISTRATION_TAXES_COST_2020D  = 730
AUTO_GAS_COST_2020D                 = 1250 # use a model output instead

# TODO: replace use of these constants with Income category specific VOTs
# sourced from USDOT Benefit-Cost Analysis Guidance  in 2020 dollars
# chrome-extension://efaidnbmnnnibpcajpcglclefindmkaj/https://www.transportation.gov/sites/dot.gov/files/2022-03/Benefit%20Cost%20Analysis%20Guidance%202022%20Update%20%28Final%29.pdf
# inflation adjustment CPI 2020, 2000 reference https://github.com/BayAreaMetro/modeling-website/wiki/InflationAssumptions
VOT_2023D_PERSONAL             = 17.8 / INFLATION_00_20 * INFLATION_00_23  # based on "All Purposes" in Table A-3
VOT_2023D_COMMERCIAL           = 32.0 / INFLATION_00_20 * INFLATION_00_23  # based on Commercial Vehicle Operators - Truck Drivers

A2_CONSTANTS = """

 - Avg hourly wage ($/hr)
    - source: ACS PUMS 2021, see M:\Data\Requests\Anup Tapase\ACS PUMS 2021 Mean Wage by Quartile.csv
 - Monetary Value of travel time (% of wage rate)
    - source: Table 5.2.11-1 https://www.vtpi.org/tca/tca0502.pdf
    - source: Table 1 (Revision - 2016 Update) https://www.transportation.gov/sites/dot.gov/files/docs/2016%20Revised%20Value%20of%20Travel%20Time%20Guidance.pdf
 - Monetary Value of travel time ($/hr)

"""

# for households and commercial
# TODO: placeholders pending adjustment post research
Q1_MEAN_HOURLY_WAGE_2023D = 16.48544
Q2_MEAN_HOURLY_WAGE_2023D = 34.40701
Q3_MEAN_HOURLY_WAGE_2023D = 59.05509
Q4_MEAN_HOURLY_WAGE_2023D = 144.79832

Q1_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D = .5
Q2_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D = .5
Q3_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D = .5
Q4_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D = .5
Q1_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D = 1
Q2_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D = 1
Q3_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D = 1

Q1_HOUSEHOLD_VOT_2023D = Q1_MEAN_HOURLY_WAGE_2023D * Q1_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
Q2_HOUSEHOLD_VOT_2023D = Q2_MEAN_HOURLY_WAGE_2023D * Q2_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
Q3_HOUSEHOLD_VOT_2023D = Q3_MEAN_HOURLY_WAGE_2023D * Q3_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
Q4_HOUSEHOLD_VOT_2023D = Q4_MEAN_HOURLY_WAGE_2023D * Q4_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
Q1_COMMERCIAL_VOT_2023D = Q1_MEAN_HOURLY_WAGE_2023D * Q1_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D
Q2_COMMERCIAL_VOT_2023D = Q2_MEAN_HOURLY_WAGE_2023D * Q2_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D
Q3_COMMERCIAL_VOT_2023D = Q3_MEAN_HOURLY_WAGE_2023D * Q3_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D

BASE_YEAR       = "2015"
FORECAST_YEAR   = "2035"
# assumptions for fatalities
# constants below are observed values for year 2015 (fatalities/year)
# copied from Box\Horizon and Plan Bay Area 2050\Equity and Performance\7_Analysis\Metrics\Metrics Development\Healthy\Fatalities Injuries\VZ_safety_calc_correction_v2.R
N_DAYS_PER_YEAR = 300 # used in Affordable1
OBS_N_MOTORIST_FATALITIES_15 = 301
OBS_N_PED_FATALITIES_15 = 127
OBS_N_BIKE_FATALITIES_15 = 27
OBS_N_MOTORIST_INJURIES_15 = 1338
OBS_N_PED_INJURIES_15 = 379
OBS_N_BIKE_INJURIES_15 = 251
OBS_INJURIES_15 = 1968  

PER_X_PEOPLE = 100000 #100k

# travel model tour and trip modes
# https://github.com/BayAreaMetro/modeling-website/wiki/TravelModes#tour-and-trip-modes
MODES_TRANSIT      = [9,10,11,12,13,14,15,16,17,18]
MODES_TAXI_TNC     = [19,20,21]
MODES_SOV          = [1,2]
MODES_HOV          = [3,4,5,6]
MODES_PRIVATE_AUTO = MODES_SOV + MODES_HOV
MODES_WALK         = [7]
MODES_BIKE         = [8]

# travel model tour purpose
# https://github.com/BayAreaMetro/modeling-website/wiki/IndividualTour
PURPOSES_COMMUTE = ['work_low','work_med','work_high','work_very high']

# travel model time periods
# https://github.com/BayAreaMetro/modeling-website/wiki/TimePeriods
TIME_PERIODS_PEAK = ['AM','PM']

METRICS_COLUMNS = [
    'grouping1',
    'grouping2',
    'grouping3',
    'modelrun_id',
    'metric_id',
    'intermediate/final', # TODO: suggest renaming this to 'metric_level' since other options are used beyond intermediate and final
    'key',
    'metric_desc',
    'year',
    'value'
]

# TODO: remove these after metrics methodology is finilzed (for debugging)
ODTRAVELTIME_FILENAME = "ODTravelTime_byModeTimeperiodIncome.csv"
# ODTRAVELTIME_FILENAME = "ODTravelTime_byModeTimeperiod_reduced_file.csv"

def trips_commute_mode_pkop(tm_run_id, metric_id):
    ################################### trips by peak/off-peak, commute/noncommute, auto/transit ###################################
    # key                       intermediate/final    metric_desc
    # [commute]_[mode]_[pkop]   top_level/E2b             trips
    # [commute]_[mode]_[pkop]   top_level/E2b             trips
    metrics_df = pd.DataFrame()
    trip_distance_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "core_summaries", "TripDistance.csv")
    tm_trips_df = pd.read_csv(trip_distance_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_trips_df), trip_distance_file))
    LOGGER.debug("tm_trips_df.head():\n{}".format(tm_trips_df.head()))

    # simplify to auto versus transit versus active
    tm_trips_df['agg_trip_mode'] = 'active'
    tm_trips_df.loc[ tm_trips_df.trip_mode.isin(MODES_TRANSIT),      'agg_trip_mode' ] = 'transit'
    tm_trips_df.loc[ tm_trips_df.trip_mode.isin(MODES_PRIVATE_AUTO), 'agg_trip_mode' ] = 'auto'
    tm_trips_df.loc[ tm_trips_df.trip_mode.isin(MODES_TAXI_TNC),     'agg_trip_mode' ] = 'other'

    # simplify to commute versus noncommute
    tm_trips_df['commute_non'] = 'noncommute'
    tm_trips_df.loc[ tm_trips_df.tour_purpose.isin(PURPOSES_COMMUTE), 'commute_non' ] = 'commute'

    # simplify to peak versus nonpeak
    tm_trips_df['peak_non'] = 'offpeak'
    tm_trips_df.loc[ tm_trips_df.timeCode.isin(TIME_PERIODS_PEAK), 'peak_non' ] = 'peak'

    # roll it up
    tm_trips_df = tm_trips_df.groupby(by=['agg_trip_mode', 'commute_non', 'peak_non']).agg({'freq':'sum'}).reset_index()
    tm_trips_df.rename(columns={'freq':'trips'}, inplace=True)
    LOGGER.debug('Aggregated tm_trips_df:\n{}'.format(tm_trips_df))

    # metrics: total trips
    metrics_trip_df = tm_trips_df.copy()
    metrics_trip_df['grouping1'] = metrics_trip_df['commute_non']
    metrics_trip_df['grouping2'] = metrics_trip_df['agg_trip_mode']
    metrics_trip_df['grouping3'] = metrics_trip_df['peak_non']
    metrics_trip_df['key'] = metrics_trip_df['commute_non'] + "_" + metrics_trip_df['agg_trip_mode'] + "_" + metrics_trip_df['peak_non']
    metrics_trip_df['intermediate/final'] = metric_id
    metrics_trip_df['metric_desc'] = 'trips'
    metrics_trip_df.rename(columns={'trips':'value'}, inplace=True)
    metrics_trip_df.drop(columns=['commute_non','agg_trip_mode','peak_non'], inplace=True)
    LOGGER.debug('metrics_trip_df:\n{}'.format(metrics_trip_df))
    metrics_df = pd.concat([metrics_df, metrics_trip_df])

    # key                       intermediate/final    metric_desc
    # [pkop]                    top_level/E2b             [mode]_commute_peak-vs-offpeak_share
    # [pkop]                    top_level/E2b             [mode]_noncommute_peak-vs-offpeak_share

    # metrics: peak vs offpeak shares
    # add column for peak_offpeak_trips = peak + nonpeak
    metrics_peak_offpeak_share_df = pd.merge(
        left  = tm_trips_df,
        right = tm_trips_df.groupby(by=['agg_trip_mode','commute_non']).agg(
                     peak_offpeak_trips = pd.NamedAgg(column='trips', aggfunc='sum')).reset_index(),
        how='left'
    )
    metrics_peak_offpeak_share_df.rename(columns={'peak_non':'key'}, inplace=True)
    metrics_peak_offpeak_share_df['intermediate/final'] = metric_id
    metrics_peak_offpeak_share_df['metric_desc'] = metrics_peak_offpeak_share_df['agg_trip_mode'] + \
                                                   "_" + metrics_peak_offpeak_share_df['commute_non'] + "_peak-vs-offpeak_share"
    metrics_peak_offpeak_share_df['value'] = metrics_peak_offpeak_share_df['trips'] / metrics_peak_offpeak_share_df['peak_offpeak_trips']
    metrics_peak_offpeak_share_df.drop(columns=['agg_trip_mode','commute_non','trips','peak_offpeak_trips'], inplace=True)
    LOGGER.debug("metrics_peak_offpeak_share_df:\n{}".format(metrics_peak_offpeak_share_df))
    metrics_df = pd.concat([metrics_df, metrics_peak_offpeak_share_df])

    # key                       intermediate/final    metric_desc
    # [mode]                    top_level/E2b             [pkop]_[commute]_mode_share
    metrics_modeshare_df = pd.merge(
        left  = tm_trips_df,
        right = tm_trips_df.groupby(by=['commute_non','peak_non']).agg(
                     allmode_trips = pd.NamedAgg(column='trips', aggfunc='sum')).reset_index(),
        how   ='left'
    )
    # LOGGER.debug("metrics_modeshare_df:\n{}".format(metrics_modeshare_df))
    metrics_modeshare_df.rename(columns={'agg_trip_mode':'key'}, inplace=True)
    metrics_modeshare_df['grouping1'] = metrics_modeshare_df['peak_non']
    metrics_modeshare_df['grouping2'] = metrics_modeshare_df['commute_non']
    metrics_modeshare_df['intermediate/final'] = metric_id
    metrics_modeshare_df['metric_desc'] = metrics_modeshare_df['peak_non'] + \
                                          "_" + metrics_modeshare_df['commute_non'] + "_mode_share"
    metrics_modeshare_df['value'] = metrics_modeshare_df['trips'] / metrics_modeshare_df['allmode_trips']
    metrics_modeshare_df.sort_values(by=['metric_desc'], inplace=True)
    metrics_modeshare_df.drop(columns=['commute_non','peak_non','trips','allmode_trips'], inplace=True)
    LOGGER.debug("metrics_modeshare_df:\n{}".format(metrics_modeshare_df))
    metrics_df = pd.concat([metrics_df, metrics_modeshare_df])
    return metrics_df

def calculate_top_level_metrics(tm_run_id, year, tm_vmt_metrics_df, tm_auto_times_df, tm_transit_times_df, tm_loaded_network_df, vmt_hh_df,tm_scen_metrics_df):
    """ Calculates top-level metrics (which are not part of the 10 metrics)
    These metrics are designed to give us overall understanding of the pathway, such as:
    - vmt (this is metric 10 but, repeated as a top level metric)
    - auto trips overall
    - auto trips by income level
    - transit trips overall
    - transit trips by income level
    - auto commute trips in peak hours
    - transit commute trips in off peak hours
    - auto commute trips in peak hours
    - transit commute trips in off peak hours
    - freeway delay
    - toll revenues from new tolling (ie exclude any express lane or bridge toll revenues)
        - freeways
        - arterials
        - cordons
    - toll revenues Q1 (ie exclude any express lane or bridge toll revenues)
        - freeways
        - arterials
        - cordons
    - toll revenues Q2 (ie exclude any express lane or bridge toll revenues)
        - freeways
        - arterials
        - cordons

    Args:
        tm_run_id (str): Travel model run ID
        [todo fill these in]
    
    Returns:
        pandas.DataFrame: with columns a subset of METRICS_COLUMNS, including 
          metric_id   = 'overall'
          modelrun_id = tm_run_id
        Metrics returned:
          key                       intermediate/final    metric_desc
          [commute]_[mode]_[pkop]   top_level             trips
          [commute]_[mode]_[pkop]   top_level             trips
          [pkop]                    top_level             [mode]_commute_peak-vs-offpeak_share
          [pkop]                    top_level             [mode]_noncommute_peak-vs-offpeak_share
          [mode]                    top_level             [pkop]_[commute]_mode_share
        where [mode] is one of auto|transit|active, 
              [pkop] is one of peak|offpeak, and 
              [commute] is one of commute|noncommute
          TODO: add others
    """
    REVENUE_METHODOLOGY_AND_ASSUMPTIONS = """
    toll revenues - from Value Tolls field in auto times --> this is daily revenue in $2000 cents
    260 days a year
    convert cents to dollars
    adjust $2000 to $2023 for YOE revenue in 2023 using CPI index
    adjust $2023 to $2035 and beyond to $2050 using inflation factor 1.03
    your output variables should be
    annual revenue
    total 15 year revenue (2035-2050)
    each of those split by four income level and other (i.e. ix/air/truck)
    """
    metric_id = 'overall'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
    LOGGER.info("Calculating {} for {}".format(metric_id, tm_run_id))

    metrics_dict = {}
    metrics_df = pd.DataFrame() # move towards putting metrics in here

    # calculate vmt (as calculated in pba50_metrics.py)
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT','daily_total_vmt',year] = tm_auto_times_df.loc[:,'Vehicle Miles'].sum()
    # # calculate hh vmt 
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT','daily_household_vmt',year] = (vmt_hh_df.loc[:,'vmt'] * vmt_hh_df.loc[:,'freq']).sum()

    # calculate auto trips (as calculated in scenarioMetrics.py)
    auto_trips_overall = 0
    auto_times_summed = tm_auto_times_df.copy().groupby('Income').agg('sum')
    for inc_level in range(1,5):
        metrics_dict['Income Level', 'Auto', grouping3, tm_run_id, metric_id,'top_level','Trips', 'inc%d' % inc_level, year] = auto_times_summed.loc['inc%d' % inc_level, 'Daily Person Trips']
        metrics_dict['Income Level', 'Auto', grouping3, tm_run_id, metric_id,'top_level','VHT', 'inc%d' % inc_level, year] = auto_times_summed.loc['inc%d' % inc_level, 'Vehicle Minutes']/60
        metrics_dict['Income Level', 'Auto', grouping3, tm_run_id, metric_id,'top_level','VMT', 'inc%d' % inc_level, year] = auto_times_summed.loc['inc%d' % inc_level, 'Vehicle Miles']
        # total auto trips
        auto_trips_overall += auto_times_summed.loc['inc%d' % inc_level, 'Daily Person Trips']
    metrics_dict[grouping1, 'Auto', grouping3, tm_run_id, metric_id,'top_level','Trips', 'Daily_total_auto_trips_overall', year] = auto_trips_overall
    # calculate vmt and trip breakdown to understand what's going on
    for auto_times_mode in ['truck', 'ix', 'air', 'zpv_tnc']:
        if auto_times_mode == 'truck':
            modegrouping = 'Truck'
        else:
            modegrouping = 'Non-Household'
        metrics_dict[modegrouping, modegrouping, grouping3, tm_run_id, metric_id,'top_level','Trips', '{}'.format(auto_times_mode), year] = tm_auto_times_df.copy().loc[(tm_auto_times_df['Mode'].str.contains(auto_times_mode) == True), 'Daily Person Trips'].sum()
        metrics_dict[modegrouping, modegrouping, grouping3, tm_run_id, metric_id,'top_level','VHT', '{}'.format(auto_times_mode), year] = tm_auto_times_df.copy().loc[(tm_auto_times_df['Mode'].str.contains(auto_times_mode) == True), 'Vehicle Minutes'].sum()/60
        metrics_dict[modegrouping, modegrouping, grouping3, tm_run_id, metric_id,'top_level','VMT', '{}'.format(auto_times_mode), year] = tm_auto_times_df.copy().loc[(tm_auto_times_df['Mode'].str.contains(auto_times_mode) == True), 'Vehicle Miles'].sum()

    # compute Fwy and Non_Fwy VMT
    vmt_df = tm_loaded_network_df.copy()
    vmt_df['total_vmt'] = (vmt_df['distance']*(vmt_df['volEA_tot']+vmt_df['volAM_tot']+vmt_df['volMD_tot']+vmt_df['volPM_tot']+vmt_df['volEV_tot']))
    vmt_df['total_vht'] = ((vmt_df['ctimEA']*vmt_df['volEA_tot']) + (vmt_df['ctimAM']*vmt_df['volAM_tot']) + (vmt_df['ctimMD']*vmt_df['volMD_tot']) + (vmt_df['ctimPM']*vmt_df['volPM_tot']) + (vmt_df['ctimEV']*vmt_df['volEV_tot']))/60
    fwy_vmt_df = vmt_df.copy().loc[(vmt_df['ft'] == 1)|(vmt_df['ft'] == 2)|(vmt_df['ft'] == 8)]
    arterial_vmt_df = vmt_df.copy().loc[(vmt_df['ft'] == 7)]
    expressway_vmt_df = vmt_df.copy().loc[(vmt_df['ft'] == 3)]
    collector_vmt_df = vmt_df.copy().loc[(vmt_df['ft'] == 4)]
    metrics_dict['Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT', 'Freeway', year] = fwy_vmt_df.loc[:,'total_vmt'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT', 'Arterial', year] = arterial_vmt_df.loc[:,'total_vmt'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT', 'Expressway', year] = expressway_vmt_df.loc[:,'total_vmt'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VMT', 'Collector', year] = collector_vmt_df.loc[:,'total_vmt'].sum()
    metrics_dict['Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VHT', 'Freeway', year] = fwy_vmt_df.loc[:,'total_vht'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VHT', 'Arterial', year] = arterial_vmt_df.loc[:,'total_vht'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VHT', 'Expressway', year] = expressway_vmt_df.loc[:,'total_vht'].sum()
    metrics_dict['Non-Freeway', grouping2, grouping3, tm_run_id, metric_id,'top_level','VHT', 'Collector', year] = collector_vmt_df.loc[:,'total_vht'].sum()
    # calculate transit trips (as calculated in scenarioMetrics.py)
    transit_trips_overall = 0
    transit_times_summed = tm_transit_times_df.copy().groupby('Income').agg('sum')
    for inc_level in range(1,5):
        metrics_dict['Income Level', 'Transit', grouping3, tm_run_id, metric_id,'top_level','Trips','Daily_total_transit_trips_inc%d' % inc_level, year] = transit_times_summed.loc['_no_zpv_inc%d' % inc_level, 'Daily Trips']
        transit_trips_overall += transit_times_summed.loc['_no_zpv_inc%d' % inc_level, 'Daily Trips']
    metrics_dict[grouping1, 'Transit', grouping3, tm_run_id, metric_id,'top_level','Trips', 'Daily_total_transit_trips_overall', year] = transit_trips_overall

    metrics_df = pd.concat([metrics_df, trips_commute_mode_pkop(tm_run_id, 'top_level')])

    
    # ################################### freeway delay ###################################
    # MTC calculates two measures of delay 
    # - congested delay, or delay that occurs when speeds are below 35 miles per hour, 
    # and total delay, or delay that occurs when speeds are below the posted speed limit.
    # https://www.vitalsigns.mtc.ca.gov/time-spent-congestion#:~:text=To%20illustrate%2C%20if%201%2C000%20vehicles,hours%20%3D%204.76%20vehicle%20hours%5D.
    fwy_network_df = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['ft'] == 1)|(tm_loaded_network_df['ft'] == 2)|(tm_loaded_network_df['ft'] == 8)]

    EA_nonzero_spd_network_df = fwy_network_df.copy().loc[(fwy_network_df['cspdEA'] > 0)]
    EA_total_delay = (EA_nonzero_spd_network_df['distance'] * EA_nonzero_spd_network_df['volEA_tot'] * ((1/EA_nonzero_spd_network_df['cspdEA']).replace(numpy.inf, 0) - (1/EA_nonzero_spd_network_df['ffs']).replace(numpy.inf, 0))).sum()
    AM_nonzero_spd_network_df = fwy_network_df.copy().loc[(fwy_network_df['cspdAM'] > 0)]
    AM_total_delay = (AM_nonzero_spd_network_df['distance'] * AM_nonzero_spd_network_df['volAM_tot'] * ((1/AM_nonzero_spd_network_df['cspdAM']).replace(numpy.inf, 0) - (1/AM_nonzero_spd_network_df['ffs']).replace(numpy.inf, 0))).sum()
    MD_nonzero_spd_network_df = fwy_network_df.copy().loc[(fwy_network_df['cspdMD'] > 0)]
    MD_total_delay = (MD_nonzero_spd_network_df['distance'] * MD_nonzero_spd_network_df['volMD_tot'] * ((1/MD_nonzero_spd_network_df['cspdMD']).replace(numpy.inf, 0) - (1/MD_nonzero_spd_network_df['ffs']).replace(numpy.inf, 0))).sum()
    PM_nonzero_spd_network_df = fwy_network_df.copy().loc[(fwy_network_df['cspdPM'] > 0)]
    PM_total_delay = (PM_nonzero_spd_network_df['distance'] * PM_nonzero_spd_network_df['volPM_tot'] * ((1/PM_nonzero_spd_network_df['cspdPM']).replace(numpy.inf, 0) - (1/PM_nonzero_spd_network_df['ffs']).replace(numpy.inf, 0))).sum()
    EV_nonzero_spd_network_df = fwy_network_df.copy().loc[(fwy_network_df['cspdEV'] > 0)]
    EV_total_delay = (EV_nonzero_spd_network_df['distance'] * EV_nonzero_spd_network_df['volEV_tot'] * ((1/EV_nonzero_spd_network_df['cspdEV']).replace(numpy.inf, 0) - (1/EV_nonzero_spd_network_df['ffs']).replace(numpy.inf, 0))).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Freeway Delay', 'daily_total_freeway_delay_veh_hrs', year] = EA_total_delay + AM_total_delay + MD_total_delay + PM_total_delay + EV_total_delay
    # calculate congested delay
    # only keep the links where the speeds  under 35 mph
    EA_speeds_below_35_df = fwy_network_df.copy().loc[(fwy_network_df['cspdEA'] < 35)]
    AM_speeds_below_35_df = fwy_network_df.copy().loc[(fwy_network_df['cspdAM'] < 35)]
    MD_speeds_below_35_df = fwy_network_df.copy().loc[(fwy_network_df['cspdMD'] < 35)]
    PM_speeds_below_35_df = fwy_network_df.copy().loc[(fwy_network_df['cspdPM'] < 35)]
    EV_speeds_below_35_df = fwy_network_df.copy().loc[(fwy_network_df['cspdEV'] < 35)]

    EA_congested_delay = (EA_speeds_below_35_df['distance'] * EA_speeds_below_35_df['volEA_tot'] * ((1/EA_speeds_below_35_df['cspdEA']).replace(numpy.inf, 0) - (1/35))).sum()
    AM_congested_delay = (AM_speeds_below_35_df['distance'] * AM_speeds_below_35_df['volAM_tot'] * ((1/AM_speeds_below_35_df['cspdAM']).replace(numpy.inf, 0) - (1/35))).sum()
    MD_congested_delay = (MD_speeds_below_35_df['distance'] * MD_speeds_below_35_df['volMD_tot'] * ((1/MD_speeds_below_35_df['cspdMD']).replace(numpy.inf, 0) - (1/35))).sum()
    PM_congested_delay = (PM_speeds_below_35_df['distance'] * PM_speeds_below_35_df['volPM_tot'] * ((1/PM_speeds_below_35_df['cspdPM']).replace(numpy.inf, 0) - (1/35))).sum()
    EV_congested_delay = (EV_speeds_below_35_df['distance'] * EV_speeds_below_35_df['volEV_tot'] * ((1/EV_speeds_below_35_df['cspdEV']).replace(numpy.inf, 0) - (1/35))).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Freeway Delay', 'daily_congested_freeway_delay_veh_hrs', year] = EA_congested_delay + AM_congested_delay + MD_congested_delay + PM_congested_delay + EV_congested_delay

    # calculate toll revenues
    
    tm_loaded_network_df_copy = tm_loaded_network_df.copy()
    network_with_tolls = tm_loaded_network_df_copy.loc[(tm_loaded_network_df_copy['TOLLCLASS'] > 1000)| (tm_loaded_network_df_copy['TOLLCLASS'] == 99)|(tm_loaded_network_df_copy['TOLLCLASS'] == 10)|(tm_loaded_network_df_copy['TOLLCLASS'] == 11)|(tm_loaded_network_df_copy['TOLLCLASS'] == 12)] 
    EA_total_tolls = (network_with_tolls['volEA_tot'] * network_with_tolls['TOLLEA_DA']).sum()/100
    AM_total_tolls = (network_with_tolls['volAM_tot'] * network_with_tolls['TOLLAM_DA']).sum()/100
    MD_total_tolls = (network_with_tolls['volMD_tot'] * network_with_tolls['TOLLMD_DA']).sum()/100
    PM_total_tolls = (network_with_tolls['volPM_tot'] * network_with_tolls['TOLLPM_DA']).sum()/100
    EV_total_tolls = (network_with_tolls['volEV_tot'] * network_with_tolls['TOLLEV_DA']).sum()/100
    daily_toll_rev_2000_dollars = EA_total_tolls + AM_total_tolls + MD_total_tolls + PM_total_tolls + EV_total_tolls
    daily_toll_rev_2023_dollars = daily_toll_rev_2000_dollars * INFLATION_00_23
    annual_toll_rev_2023_dollars = daily_toll_rev_2023_dollars * REVENUE_DAYS_PER_YEAR
    annual_toll_rev_2035_dollars = annual_toll_rev_2023_dollars*INFLATION_FACTOR**(2035-2023)
    # compute sum of geometric series for year of expenditure value
    fifteen_year_toll_rev_2050_dollars = (annual_toll_rev_2035_dollars * (1- INFLATION_FACTOR**15))/(1 - INFLATION_FACTOR)
    
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', 'Daily_toll_revenues_from_new_tolling_2000$', year] = daily_toll_rev_2000_dollars
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', 'Daily_toll_revenues_from_new_tolling_2035$', year] = annual_toll_rev_2035_dollars/260
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', 'Annual_toll_revenues_from_new_tolling_2035$', year] = annual_toll_rev_2035_dollars
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', '15_yr_toll_revenues_from_new_tolling_YOE$', year] = fifteen_year_toll_rev_2050_dollars

    # NEED HELP FROM FMS TEAM --> RE: INCOME ASSIGNMENT
    # calculate toll revenues by income quartile (calculation from scenarioMetrics.py)
    toll_revenues_overall = 0
    if 'Path3' in tm_run_id:
        # 'Cordon Tolls' was the old column; 'Cordon Tolls with discount' is newer
        toll_revenue_column = 'Cordon Tolls' if 'Cordon Tolls' in auto_times_summed.columns else 'Cordon tolls with discount'
    else:
        # 'Value Tolls' was the old column; 'Value Tolls with discount' is newer
        toll_revenue_column = 'Value Tolls' if 'Value Tolls' in auto_times_summed.columns else 'Value Tolls with discount'
    for inc_level in range(1,5):
        tm_tot_hh_incgroup = tm_scen_metrics_df.loc[(tm_scen_metrics_df['metric_name'] == "total_households_inc%d" % inc_level),'value'].item()
        incgroup_dailytolls = (auto_times_summed.loc['inc%d' % inc_level, toll_revenue_column]/100)
        metrics_dict["Inc %d" % inc_level, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', 'Daily revenue (includes express lane, 2000$)', year] = incgroup_dailytolls
        metrics_dict["Inc %d" % inc_level, grouping2, grouping3, tm_run_id, metric_id,'top_level','Tolls', 'Average Daily Tolls per Household', year] = incgroup_dailytolls/tm_tot_hh_incgroup
        toll_revenues_overall += incgroup_dailytolls
    # use as a check for calculated value above. should be in the same ballpark. calculate ratio and use for links?
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'top_level','Toll Revenues', 'Daily revenue (includes express lane, 2000$)', year] = toll_revenues_overall

    # fill in generic dataframe columns
    metrics_df['metric_id'] = metric_id
    metrics_df['modelrun_id'] = tm_run_id
    metrics_df['year'] = tm_run_id[:4]
    # combine dict and dataframe
    metrics_df = pd.concat([metrics_df, metrics_dict_to_df(metrics_dict)])
    metrics_df = metrics_df[METRICS_COLUMNS] # reorder columns
    # LOGGER.debug("metrics_df from calculate_top_level_metrics:\n{}".format(metrics_df))
    return metrics_df

def calculate_change_between_run_and_base(tm_run_id, BASE_SCENARIO_RUN_ID, year, metric_id, metrics_dict):
    #function to compare two runs and enter difference as a metric in dictionary
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
    metrics_dict_series = pd.Series(metrics_dict)
    metrics_dict_df  = metrics_dict_series.to_frame().reset_index()
    metrics_dict_df.columns = ['grouping1', 'grouping2', 'grouping3', 'modelrun_id','metric_id','intermediate/final','key','metric_desc','year','value']
    #     make a list of the metrics from the run of interest to iterate through and calculate a difference with
    metrics_list = metrics_dict_df.copy().loc[(metrics_dict_df['modelrun_id'] == tm_run_id)]
    metrics_list = metrics_list.loc[(metrics_dict_df['metric_id'].str.contains(metric_id) == True)]['metric_desc']
    # iterate through the list
    # add in grouping field
    key = 'Change'
    for metric in metrics_list:
        if (('_AM' in metric)):
            temp = metric.split('_AM')[0]
            key = temp.split('travel_time_')[-1]
        elif ('across_key_corridors' in metric):
            key = 'Average Across Corridors'

        val_run = metrics_dict_df.copy().loc[(metrics_dict_df['modelrun_id'] == tm_run_id)].loc[(metrics_dict_df['metric_desc'] == metric)].iloc[0]['value']
        val_base = metrics_dict_df.copy().loc[(metrics_dict_df['modelrun_id'] == BASE_SCENARIO_RUN_ID)].loc[(metrics_dict_df['metric_desc'] == metric)].iloc[0]['value']
        metrics_dict[key, grouping2, grouping3, tm_run_id, metric_id,'debug step','By Corridor','change_in_{}'.format(metric),year] = (val_run-val_base)
        metrics_dict[key, grouping2, grouping3, tm_run_id, metric_id,'debug step','By Corridor','pct_change_in_{}'.format(metric),year] = ((val_run-val_base)/val_base)




def calculate_Affordable1_transportation_costs(tm_run_id: str) -> pd.DataFrame:
    """ Calculates Affordable 1: Transportation costs as a share of household income

    Args:
        tm_run_id (str): Travel model run ID

    Returns:
        pd.DataFrame: with columns a subset of METRICS_COLUMNS, including 
          metric_id   = 'Affordable 1'
          modelrun_id = tm_run_id
        Metrics returned:


        where [income_category] is one of: incQ1, incQ2, incQ1Q2, all_inc, referring to travel model income quartiles
        and   [hhld_travel_category] is based on whether the houseld makes private auto trips, transit trips, both, or neither, 
                                     so it is one of auto_and_transit, auto_no_transit, transit_no_auto, no_auto_no_transit
        See travel-cost-by-income-driving-households.r for more

    """
    METRIC_ID = "Affordable 1"
    LOGGER.info("Calculating {} for {}".format(METRIC_ID, tm_run_id))

    travel_cost_by_travel_hhld_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "core_summaries", "travel-cost-hhldtraveltype.csv")
    travel_cost_df = pd.read_csv(travel_cost_by_travel_hhld_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(travel_cost_df), travel_cost_by_travel_hhld_file))
    LOGGER.debug("  Head:\n{}".format(travel_cost_df.head()))

    # columns are: incQ, incQ_label, home_taz, hhld_travel, 
    #              num_hhlds, num_persons, num_auto_trips, num_transit_trips, 
    #              total_auto_cost, total_transit_cost, total_cost, total_hhld_autos, total_hhld_income
    #              total_auto_op_cost, total_bridge_toll, total_cordon_toll, total_value_toll, 
    #              total_fare, total_drv_trn_op_cost, total_taxitnc_cost,
    #              total_detailed_auto_cost, total_detailed_transit_cost
    # convert incQ from number to string
    travel_cost_df['incQ'] = "incQ" + travel_cost_df['incQ'].astype('str')
    # Summarize to incQ_label, hhld_travel segments
    travel_cost_df = travel_cost_df.groupby(by=['incQ','hhld_travel']).agg({
        'num_hhlds':            'sum',
        'total_auto_op_cost':      'sum',
        'total_detailed_auto_cost':      'sum',
        'total_detailed_transit_cost':      'sum',
        'total_parking_cost':      'sum',
        'total_bridge_toll':      'sum',
        'total_value_toll':      'sum',
        'total_cordon_toll':      'sum',
        'total_fare':   'sum',
        'total_drv_trn_op_cost':   'sum',
        'total_taxitnc_cost':   'sum',
        'total_hhld_autos':     'sum',
        'total_hhld_income':    'sum',
        'num_auto_trips':    'sum',
        'num_transit_trips':    'sum',
        'num_taxitnc_trips':    'sum'
    })
    # note: the index is not reset so it's a MultiIndex with incQ, hhld_travel
    LOGGER.debug("  travel_cost_df:\n{}".format(travel_cost_df))

    # add variable costs to df:
    #   Ops cost (includes fuel+maintenance)
    #   Parking costs
    #   Bridge Toll costs
    #   Value Toll costs
    #   Transit fare costs

    # annualize and convert daily costs from 2000 cents to 2023 dollars 
    travel_cost_df['total_auto_op_cost_annual_2023d']    = travel_cost_df['total_auto_op_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_parking_cost_annual_2023d']    = travel_cost_df['total_parking_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_bridge_toll_cost_annual_2023d']    = travel_cost_df['total_bridge_toll']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_value_toll_cost_annual_2023d']    = travel_cost_df['total_value_toll']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_cordon_toll_cost_annual_2023d']    = travel_cost_df['total_cordon_toll']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_transit_op_cost_annual_2023d'] = travel_cost_df['total_fare']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_drive_to_transit_cost_annual_2023d'] = travel_cost_df['total_drv_trn_op_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    
    travel_cost_df['total_taxitnc_cost_annual_2023d'] = travel_cost_df['total_taxitnc_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23

    travel_cost_df['total_detailed_auto_cost_annual_2023d'] = travel_cost_df['total_detailed_auto_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23
    travel_cost_df['total_detailed_transit_cost_annual_2023d'] = travel_cost_df['total_detailed_transit_cost']*N_DAYS_PER_YEAR * 0.01 * INFLATION_00_23

    # add fixed costs to df:
    #   ownership + finance
    #   insurance
    #   registration/taxes

    # add auto ownership costs (by income)
    travel_cost_df.loc['incQ1', 'total_auto_own_finance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_OWNERSHIP_COST_2020D + AUTO_FINANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ2', 'total_auto_own_finance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_OWNERSHIP_COST_2020D + AUTO_FINANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ3', 'total_auto_own_finance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_OWNERSHIP_COST_2020D + AUTO_FINANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ4', 'total_auto_own_finance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_OWNERSHIP_COST_2020D + AUTO_FINANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23

    # add auto insurance costs (by income)
    travel_cost_df.loc['incQ1', 'total_auto_insurance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_INSURANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ2', 'total_auto_insurance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_INSURANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ3', 'total_auto_insurance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_INSURANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ4', 'total_auto_insurance_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_INSURANCE_COST_2020D) / INFLATION_00_20 * INFLATION_00_23

    # add auto registration/taxes costs (by income)
    travel_cost_df.loc['incQ1', 'total_auto_registration_taxes_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_REGISTRATION_TAXES_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ2', 'total_auto_registration_taxes_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_REGISTRATION_TAXES_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ3', 'total_auto_registration_taxes_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_REGISTRATION_TAXES_COST_2020D) / INFLATION_00_20 * INFLATION_00_23
    travel_cost_df.loc['incQ4', 'total_auto_registration_taxes_cost_annual_2023d'] = travel_cost_df['total_hhld_autos']*(AUTO_REGISTRATION_TAXES_COST_2020D) / INFLATION_00_20 * INFLATION_00_23

    # all transportation costs
    travel_cost_df['total_transportation_cost_annual_2023d']       = \
        travel_cost_df['total_detailed_auto_cost_annual_2023d']          + \
        travel_cost_df['total_detailed_transit_cost_annual_2023d']       + \
        travel_cost_df['total_auto_own_finance_cost_annual_2023d'] + \
        travel_cost_df['total_auto_insurance_cost_annual_2023d']   + \
        travel_cost_df['total_auto_registration_taxes_cost_annual_2023d'] + \
        travel_cost_df['total_taxitnc_cost_annual_2023d']

    # and finally annual household income from 2000 dollars to 2023 dollars
    travel_cost_df['total_hhld_income_annual_2023d']    = travel_cost_df['total_hhld_income']*INFLATION_00_23

    # create a combined incQ1Q2 and all_ind
    incQ1Q2_df = \
        travel_cost_df.loc['incQ1'] + \
        travel_cost_df.loc['incQ2'] 
    all_inc_df = \
        travel_cost_df.loc['incQ1'] + \
        travel_cost_df.loc['incQ2'] + \
        travel_cost_df.loc['incQ3'] + \
        travel_cost_df.loc['incQ4']
    # make index consistent and add to our table
    incQ1Q2_df.index = pd.MultiIndex.from_arrays([['incQ1Q2']*len(incQ1Q2_df.index.tolist()), incQ1Q2_df.index.tolist()], names=('incQ','hhld_travel'))
    all_inc_df.index = pd.MultiIndex.from_arrays([['all_inc']*len(all_inc_df.index.tolist()), all_inc_df.index.tolist()], names=('incQ','hhld_travel'))
    travel_cost_df = pd.concat([travel_cost_df, incQ1Q2_df, all_inc_df])
    LOGGER.debug("   travel_cost_df:\n{}".format(travel_cost_df))

    # calculate average per household
    travel_cost_df['avg_num_autos_per_hhld']                                 = travel_cost_df['total_hhld_autos']                                   /travel_cost_df['num_hhlds']
    travel_cost_df['avg_hhld_income_annual_2023d_per_hhld']                  = travel_cost_df['total_hhld_income_annual_2023d']                     /travel_cost_df['num_hhlds']
    travel_cost_df['avg_auto_op_cost_annual_2023d_per_hhld']                 = travel_cost_df['total_auto_op_cost_annual_2023d']                    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_parking_cost_annual_2023d_per_hhld']                 = travel_cost_df['total_parking_cost_annual_2023d']                    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_bridge_toll_cost_annual_2023d_per_hhld']             = travel_cost_df['total_bridge_toll_cost_annual_2023d']                    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_value_toll_cost_annual_2023d_per_hhld']              = travel_cost_df['total_value_toll_cost_annual_2023d']                    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_cordon_toll_cost_annual_2023d_per_hhld']             = travel_cost_df['total_cordon_toll_cost_annual_2023d']                    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_transit_op_cost_annual_2023d_per_hhld']              = travel_cost_df['total_transit_op_cost_annual_2023d']                 /travel_cost_df['num_hhlds']
    travel_cost_df['avg_drive_to_transit_cost_annual_2023d_per_hhld']        = travel_cost_df['total_drive_to_transit_cost_annual_2023d']                 /travel_cost_df['num_hhlds']
    travel_cost_df['avg_taxitnc_cost_annual_2023d_per_hhld']                 = travel_cost_df['total_taxitnc_cost_annual_2023d']                 /travel_cost_df['num_hhlds']
    travel_cost_df['avg_auto_own_finance_cost_annual_2023d_per_hhld']        = travel_cost_df['total_auto_own_finance_cost_annual_2023d']           /travel_cost_df['num_hhlds']
    travel_cost_df['avg_auto_insurance_cost_annual_2023d_per_hhld']          = travel_cost_df['total_auto_insurance_cost_annual_2023d']             /travel_cost_df['num_hhlds']
    travel_cost_df['avg_auto_registration_taxes_cost_annual_2023d_per_hhld'] = travel_cost_df['total_auto_registration_taxes_cost_annual_2023d']    /travel_cost_df['num_hhlds']
    travel_cost_df['avg_transportation_cost_annual_2023d_per_hhld']          = travel_cost_df['total_transportation_cost_annual_2023d']             /travel_cost_df['num_hhlds']
    # calculate average per trip
    travel_cost_df['avg_auto_cost_annual_2023d_per_trip']             = (travel_cost_df['total_detailed_auto_cost_annual_2023d'] + \
                                                                             travel_cost_df['total_auto_own_finance_cost_annual_2023d'] + \
                                                                             travel_cost_df['total_auto_insurance_cost_annual_2023d']   + \
                                                                             travel_cost_df['total_auto_registration_taxes_cost_annual_2023d'])     /travel_cost_df['num_auto_trips']
    travel_cost_df['avg_transit_cost_annual_2023d_per_trip']          = (travel_cost_df['total_detailed_transit_cost_annual_2023d'] + \
                                                                             travel_cost_df['total_auto_own_finance_cost_annual_2023d'] + \
                                                                             travel_cost_df['total_auto_insurance_cost_annual_2023d']   + \
                                                                             travel_cost_df['total_auto_registration_taxes_cost_annual_2023d'])     /travel_cost_df['num_transit_trips']
    travel_cost_df['avg_taxitnc_cost_annual_2023d_per_trip']          = travel_cost_df['total_taxitnc_cost_annual_2023d']                                  /travel_cost_df['num_taxitnc_trips']
    travel_cost_df['avg_transportation_cost_annual_2023d_per_trip']   = travel_cost_df['total_transportation_cost_annual_2023d']                    /(travel_cost_df['num_auto_trips'] + \
                                                                                                                                                      travel_cost_df['num_transit_trips'] + \
                                                                                                                                                      travel_cost_df['num_taxitnc_trips'])
    # calculate pct of income
    travel_cost_df['auto_op_cost_pct_of_income']                 = travel_cost_df['total_auto_op_cost_annual_2023d']                 /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['parking_cost_pct_of_income']                 = travel_cost_df['total_parking_cost_annual_2023d']                 /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['bridge_toll_cost_pct_of_income']             = travel_cost_df['total_bridge_toll_cost_annual_2023d']                 /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['value_toll_cost_pct_of_income']              = travel_cost_df['total_value_toll_cost_annual_2023d']                 /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['cordon_toll_cost_pct_of_income']             = travel_cost_df['total_cordon_toll_cost_annual_2023d']                 /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['transit_op_cost_pct_of_income']              = travel_cost_df['total_transit_op_cost_annual_2023d']              /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['drive_to_transit_cost_pct_of_income']        = travel_cost_df['total_drive_to_transit_cost_annual_2023d']              /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['taxitnc_cost_pct_of_income']                 = travel_cost_df['total_taxitnc_cost_annual_2023d']              /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['auto_own_finance_cost_pct_of_income']        = travel_cost_df['total_auto_own_finance_cost_annual_2023d']        /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['auto_insurance_cost_pct_of_income']          = travel_cost_df['total_auto_insurance_cost_annual_2023d']          /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['auto_registration_taxes_cost_pct_of_income'] = travel_cost_df['total_auto_registration_taxes_cost_annual_2023d'] /travel_cost_df['total_hhld_income_annual_2023d']
    travel_cost_df['transportation_cost_pct_of_income']          = travel_cost_df['total_transportation_cost_annual_2023d']          /travel_cost_df['total_hhld_income_annual_2023d']

    # package for returning
    # create key
    travel_cost_df.reset_index(drop=False, inplace=True)
    travel_cost_df['key'] = travel_cost_df['incQ'] + " " + travel_cost_df['hhld_travel']

    # drop unused rows
    travel_cost_df = travel_cost_df.loc[ (travel_cost_df['incQ'] != 'incQ3') & (travel_cost_df['incQ'] != 'incQ4') ]

    # drop unused columns
    travel_cost_df.drop(columns=[
        'incQ', 'hhld_travel', # now in key
        'total_auto_op_cost',
        'total_parking_cost',
        'total_bridge_toll',
        'total_value_toll',
        'total_cordon_toll',
        'total_fare',
        'total_drv_trn_op_cost',
        'total_taxitnc_cost',
        'total_detailed_auto_cost',
        'total_detailed_transit_cost',
        'total_hhld_income',
        'total_auto_op_cost_annual_2023d',
        'total_parking_cost_annual_2023d',
        'total_bridge_toll_cost_annual_2023d',
        'total_value_toll_cost_annual_2023d',
        'total_cordon_toll_cost_annual_2023d',
        'total_transit_op_cost_annual_2023d',
        'total_drive_to_transit_cost_annual_2023d',
        'total_taxitnc_cost_annual_2023d',
        'total_detailed_auto_cost_annual_2023d',
        'total_detailed_transit_cost_annual_2023d',
        'total_auto_own_finance_cost_annual_2023d',
        'total_auto_insurance_cost_annual_2023d',
        'total_auto_registration_taxes_cost_annual_2023d',
        'total_transportation_cost_annual_2023d',
        'total_hhld_income_annual_2023d'], 
        inplace=True)

    LOGGER.debug("  travel_cost_df:\n{}".format(travel_cost_df))
    # move columns to rows
    metrics_df = pd.melt(travel_cost_df,
                         id_vars=['key'],
                         var_name='metric_desc',
                         value_name='value')
    metrics_df['intermediate/final'] = 'intermediate'
    metrics_df.loc[ metrics_df['metric_desc'].str.endswith('_pct_of_income'), 'intermediate/final'] = 'final'
    metrics_df['modelrun_id'] = tm_run_id
    metrics_df['year'] = tm_run_id[:4]
    metrics_df['metric_id'] = METRIC_ID
    # add grouping for Tableau view
    metrics_df.loc[ metrics_df['metric_desc'] == 'num_hhlds', 'grouping1'] = 'Households'
    metrics_df.loc[ metrics_df['metric_desc'] == 'avg_hhld_income_annual_2023d_per_hhld', 'grouping1'] = 'Households'
    metrics_df.loc[ metrics_df['metric_desc'] == 'avg_num_autos_per_hhld', 'grouping1'] = 'Households'
    metrics_df.loc[ metrics_df['metric_desc'] == 'avg_auto_own_finance_cost_annual_2023d_per_hhld', 'grouping1'] = 'Fixed Costs'
    metrics_df.loc[ metrics_df['metric_desc'] == 'avg_auto_insurance_cost_annual_2023d_per_hhld', 'grouping1'] = 'Fixed Costs'
    metrics_df.loc[ metrics_df['metric_desc'] == 'avg_auto_registration_taxes_cost_annual_2023d_per_hhld', 'grouping1'] = 'Fixed Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('auto_op_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('parking_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('bridge_toll_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('value_toll_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('cordon_toll_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('transit_op_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('drive_to_transit_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('taxitnc_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('transportation_cost') == True , 'grouping1'] = 'Total Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('cost_annual_2023d_per_hhld') == True , 'grouping2'] = 'cost per household'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('_cost_pct_of_income') == True , 'grouping2'] = 'cost percent of income'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('_per_trip') == True , 'grouping2'] = 'cost per trip'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('auto_cost') == True , 'grouping1'] = 'Variable Costs'
    metrics_df.loc[ metrics_df['metric_desc'].str.contains('transit_cost') == True , 'grouping1'] = 'Variable Costs'

    LOGGER.debug("  returning:\n{}".format(metrics_df))

    return metrics_df



def calculate_auto_travel_time(tm_run_id,metric_id, year,network,metrics_dict):
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
    sum_of_weights = 0 #sum of weights (vmt of corridor) to be used for weighted average 
    total_weighted_travel_time = 0 #sum for numerator
    n = 0 #counter for simple average 
    total_travel_time = 0 #numerator for simple average 

    for i in minor_groups:
        #     add minor ampm ctim to metric dict
        minor_group_am_df = network.loc[network['Grouping minor_AMPM'] == i+'_AM']
        minor_group_am = sum_grouping(minor_group_am_df,'AM')
        
        # vmt to be used for weighted averages
        # create df to pull vmt from to use for weighted average
        # for simplicity of calculation, always using the base run VMT
        index_a_b = minor_group_am_df.copy()[['a_b']]
        network_for_vmt_df = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
        vmt_minor_grouping_AM = (network_for_vmt_df['volAM_tot'] * network_for_vmt_df['distance']).sum()

        # check for length //can remove later
        length_of_grouping = (minor_group_am_df['distance']).sum()
        metrics_dict[i, grouping2, grouping3, BASE_SCENARIO_RUN_ID,metric_id,'debug step','By Corridor','%s' % i + '_AM_length',year] = length_of_grouping

        metrics_dict[i, grouping2, grouping3, BASE_SCENARIO_RUN_ID,metric_id,'debug step','By Corridor','%s' % i + '_AM_vmt',year] = vmt_minor_grouping_AM

        # add travel times to metric dict
        metrics_dict[i, 'Travel Time', grouping3, tm_run_id,metric_id,'extra','By Corridor','travel_time_%s' % i + '_AM',year] = minor_group_am
        # add average speed weighted by link distance
        try:
            metrics_dict[i, 'Travel Time', grouping3, tm_run_id,metric_id,'extra','By Corridor','average_speed_%s' % i + '_AM',year] = numpy.average(a = network_for_vmt_df['cspdAM'], weights = network_for_vmt_df['distance'])
        except:
            metrics_dict[i, 'Travel Time', grouping3, tm_run_id,metric_id,'extra','By Corridor','average_speed_%s' % i + '_AM',year] = 0

        # weighted AM,PM travel times (by vmt)
        weighted_AM_travel_time_by_vmt = minor_group_am * vmt_minor_grouping_AM




def calculate_Affordable2_ratio_time_cost(tm_run_id, year, tm_loaded_network_df, network_links, metrics_dict):
    # 2) Ratio of value of auto travel time savings to incremental toll costs

    # borrow from pba metrics calculate_Connected2_hwy_traveltimes(), but only for corridor disaggregation (and maybe commercial vs private vehicle. need to investigate income cat further)
    # make sure to run after the comparison functions have been run, as this takes them as inputs from the metrics dict
    # takes Reliable 1 metric inputs
    # will need to compute a new average across corridors, since we are only interested in the AM period
    metric_id = 'Affordable 2'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
  
    network_with_nonzero_tolls = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['TOLLCLASS'] > 1000) | (tm_loaded_network_df['TOLLCLASS'] == 99)] # might add this in to provide a denominator to ratios in pathway 3: |(tm_loaded_network_df['TOLLCLASS'] == 10)|(tm_loaded_network_df['TOLLCLASS'] == 11)|(tm_loaded_network_df['TOLLCLASS'] == 12)]
    network_with_nonzero_tolls = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['USEAM'] == 1)&(tm_loaded_network_df['ft'] != 6)]
    network_with_nonzero_tolls['sum of tolls'] = network_with_nonzero_tolls['TOLLAM_DA'] + network_with_nonzero_tolls['TOLLAM_LRG'] + network_with_nonzero_tolls['TOLLAM_S3']
    # check if run has all lane tolling, if not return 0 for this metric 
    if (network_with_nonzero_tolls['sum of tolls'].sum() == 0):
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Private Auto: All Households','average_ratio_auto_time_savings_to_toll_costs_across_corridors_weighted_by_vmt',year] = 0
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Private Auto: Very Low Income Households','average_ratio_auto_time_savings_to_toll_costs_across_corridors_inc1_weighted_by_vmt',year] = 0
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Private Auto: Very Low Income Households','average_ratio_auto_time_savings_to_toll_costs_across_corridors_inc2_weighted_by_vmt',year] = 0
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Commercial Vehicle','average_ratio_truck_time_savings_to_toll_costs_across_corridors_weighted_by_vmt',year] = 0
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','High Occupancy Vehicle','average_ratio_hov_time_savings_to_toll_costs_across_corridors_weighted_by_vmt',year] = 0
        return
    network_with_nonzero_tolls = network_with_nonzero_tolls.loc[(network_with_nonzero_tolls['sum of tolls'] > 1)]
    index_a_b = network_with_nonzero_tolls.copy()[['a_b']]
    network_with_nonzero_tolls_base = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
    calculate_auto_travel_time(tm_run_id,metric_id, year,network_with_nonzero_tolls,metrics_dict)
    calculate_auto_travel_time(BASE_SCENARIO_RUN_ID,metric_id, year,network_with_nonzero_tolls_base,metrics_dict)
    # ----calculate difference between runs----

    # run comparisons
    calculate_change_between_run_and_base(tm_run_id, BASE_SCENARIO_RUN_ID, year, 'Affordable 2', metrics_dict)

    metrics_dict_series = pd.Series(metrics_dict)
    metrics_dict_df  = metrics_dict_series.to_frame().reset_index()
    metrics_dict_df.columns = ['grouping1', 'grouping2', 'grouping3', 'modelrun_id','metric_id','intermediate/final','key','metric_desc','year','value']
    corridor_vmt_df = metrics_dict_df.copy().loc[(metrics_dict_df['metric_desc'].str.contains('_AM_vmt') == True)&(metrics_dict_df['metric_desc'].str.contains('change') == False)]
    # simplify df to relevant model run
    metrics_dict_df = metrics_dict_df.copy().loc[(metrics_dict_df['modelrun_id'].str.contains(tm_run_id) == True)]
    #make a list of the metrics from the run of interest to iterate through and calculate numerator of ratio with
    metrics_list = metrics_dict_df.loc[(metrics_dict_df['metric_desc'].str.startswith('change_in_travel_time_') == True)&(metrics_dict_df['metric_desc'].str.contains('_AM') == True)&(metrics_dict_df['metric_desc'].str.contains('vmt') == False)]['metric_desc'] 

    # the list of metrics should have the name of the corridor. split on 'change_in_avg' and pick the end part. if empty, will be final ratio, use this for other disaggregations
    # total tolls and time savings variables to be used for average
    # for weighted average
    sum_of_weighted_ratio_auto_time_savings_to_toll_costs = 0
    sum_of_weighted_ratio_truck_time_savings_to_toll_costs = 0
    sum_of_weighted_ratio_hov_time_savings_to_toll_costs = 0
    sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc1 = 0
    sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc2 = 0
    sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc3 = 0
    sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc4 = 0
    sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc1 = 0
    sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc2 = 0
    sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc3 = 0

    # for simple average
    sum_of_ratio_auto_time_savings_to_toll_costs = 0
    sum_of_ratio_auto_time_savings_to_toll_costs_inc1 = 0
    sum_of_ratio_auto_time_savings_to_toll_costs_inc2 = 0
    sum_of_ratio_auto_time_savings_to_toll_costs_inc3 = 0
    sum_of_ratio_auto_time_savings_to_toll_costs_inc4 = 0
    sum_of_ratio_truck_time_savings_to_toll_costs_inc1 = 0
    sum_of_ratio_truck_time_savings_to_toll_costs_inc2 = 0
    sum_of_ratio_truck_time_savings_to_toll_costs_inc3 = 0
    sum_of_ratio_hov_time_savings_to_toll_costs = 0

    sum_of_weights = 0 #sum of weights (length of corridor) to be used for weighted average 
    n = 0 #counter to serve as denominator 
    # iterate through list
    for metric in metrics_list:
        minor_grouping_corridor = metric.split('travel_time_')[1]

        # calculate average vmt
        minor_grouping_vmt = corridor_vmt_df.loc[corridor_vmt_df['metric_desc'] == (minor_grouping_corridor + '_vmt')].iloc[0]['value']
        # simplify df to relevant metric
        metric_row = metrics_dict_df.loc[(metrics_dict_df['metric_desc'].str.contains(metric) == True)]
        if (minor_grouping_vmt == 0): #check to make sure there is traffic on the link
            time_savings_minutes = 0
            time_savings_in_hours = 0
        else:
            time_savings_minutes = (metric_row.iloc[0]['value']) * (-1) #make time savings reflected as a positive value when there is a decrease in travel time (and vice versa)
            time_savings_in_hours = time_savings_minutes/60
        # ____will need to restructure this whole section to include all the calculations here 
        # ____as done for denominator because there is a need to filter the df by toll class and tolls paid
        # ____consider including a call to change_in function here. first define the metrics needed

        # define key for grouping field, consistent with section above
        key = minor_grouping_corridor.split('_AM')[0]

        priv_auto_travel_time_savings_minor_grouping = time_savings_in_hours * VOT_2023D_PERSONAL
        commercial_vehicle_travel_time_savings_minor_grouping = time_savings_in_hours * VOT_2023D_COMMERCIAL

        # Q1 HH numerator: travel time savings
        q1_household_travel_time_savings_minor_grouping = time_savings_in_hours * Q1_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'extra','Household','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'extra','Household','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Household','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q1_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time (% of wage rate)',year] = Q1_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time ($/hr)',year] = Q1_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time savings',year] = q1_household_travel_time_savings_minor_grouping

        # Q2 HH numerator: travel time savings
        q2_household_travel_time_savings_minor_grouping = time_savings_in_hours * Q2_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'extra','Household','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'extra','Household','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Household','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q2_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time (% of wage rate)',year] = Q2_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time ($/hr)',year] = Q2_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time savings',year] = q2_household_travel_time_savings_minor_grouping

        # Q3 HH numerator: travel time savings
        q3_household_travel_time_savings_minor_grouping = time_savings_in_hours * Q3_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'extra','Household','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'extra','Household','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Household','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q3_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time (% of wage rate)',year] = Q3_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time ($/hr)',year] = Q3_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time savings',year] = q3_household_travel_time_savings_minor_grouping

        # Q4 HH numerator: travel time savings
        q4_household_travel_time_savings_minor_grouping = time_savings_in_hours * Q4_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'extra','Household','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'extra','Household','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'intermediate','Household','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q4_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time (% of wage rate)',year] = Q4_HOUSEHOLD_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time ($/hr)',year] = Q4_HOUSEHOLD_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc4', tm_run_id, metric_id,'intermediate','Household','Monetary Value of travel time savings',year] = q4_household_travel_time_savings_minor_grouping

        # Q1 Commercial Vehicle numerator: travel time savings
        q1_commercial_travel_time_savings_minor_grouping = time_savings_in_hours * Q1_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Commercial','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q1_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time (% of wage rate)',year] = Q1_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time ($/hr)',year] = Q1_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc1', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time savings',year] = q1_commercial_travel_time_savings_minor_grouping

        # Q2 Commercial Vehicle numerator: travel time savings
        q2_commercial_travel_time_savings_minor_grouping = time_savings_in_hours * Q2_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Commercial','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q2_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time (% of wage rate)',year] = Q2_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time ($/hr)',year] = Q2_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc2', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time savings',year] = q2_commercial_travel_time_savings_minor_grouping

        # Q3 Commercial Vehicle numerator: travel time savings
        q3_commercial_travel_time_savings_minor_grouping = time_savings_in_hours * Q3_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_minutes_{}_'.format(minor_grouping_corridor),year] = time_savings_minutes 
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'extra','Commercial','auto_time_savings_hours_{}'.format(minor_grouping_corridor),year] = time_savings_in_hours
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Commercial','Avg hourly wage ($/hr)'.format(round(VOT_2023D_PERSONAL)),year] = Q3_MEAN_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time (% of wage rate)',year] = Q3_COMMERCIAL_VOT_PCT_HOURLY_WAGE_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time ($/hr)',year] = Q3_COMMERCIAL_VOT_2023D
        metrics_dict[key, 'Travel Time', 'inc3', tm_run_id, metric_id,'intermediate','Commercial','Monetary Value of travel time savings',year] = q3_commercial_travel_time_savings_minor_grouping

        # calculate the denominator: incremental toll costs (for PA CV and HOV) 
        # by filtering for the links on the corridor and summing across them
        DA_incremental_toll_costs_minor_grouping = network_with_nonzero_tolls.loc[(network_with_nonzero_tolls['Grouping minor_AMPM'].str.contains(minor_grouping_corridor) == True), 'TOLLAM_DA'].sum()/100 * INFLATION_00_23
        LRG_incremental_toll_costs_minor_grouping = network_with_nonzero_tolls.loc[(network_with_nonzero_tolls['Grouping minor_AMPM'].str.contains(minor_grouping_corridor) == True), 'TOLLAM_LRG'].sum()/100 * INFLATION_00_23
        S3_incremental_toll_costs_minor_grouping = network_with_nonzero_tolls.loc[(network_with_nonzero_tolls['Grouping minor_AMPM'].str.contains(minor_grouping_corridor) == True), 'TOLLAM_S3'].sum()/100 * INFLATION_00_23
        DA_incremental_toll_costs_inc1_minor_grouping = (DA_incremental_toll_costs_minor_grouping * Q1_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        DA_incremental_toll_costs_inc2_minor_grouping = (DA_incremental_toll_costs_minor_grouping * Q2_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        DA_incremental_toll_costs_inc3_minor_grouping = (DA_incremental_toll_costs_minor_grouping * Q3_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        DA_incremental_toll_costs_inc4_minor_grouping = (DA_incremental_toll_costs_minor_grouping * Q4_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        LRG_incremental_toll_costs_inc1_minor_grouping = (LRG_incremental_toll_costs_minor_grouping * Q1_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        LRG_incremental_toll_costs_inc2_minor_grouping = (LRG_incremental_toll_costs_minor_grouping * Q2_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)
        LRG_incremental_toll_costs_inc3_minor_grouping = (LRG_incremental_toll_costs_minor_grouping * Q3_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS)

        metrics_dict[key, 'Toll Costs (2023$)', 'inc1', tm_run_id, metric_id,'intermediate','Household','auto_toll_costs',year] = DA_incremental_toll_costs_inc1_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc2', tm_run_id, metric_id,'intermediate','Household','auto_toll_costs',year] = DA_incremental_toll_costs_inc2_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc3', tm_run_id, metric_id,'intermediate','Household','auto_toll_costs',year] = DA_incremental_toll_costs_inc3_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc4', tm_run_id, metric_id,'intermediate','Household','auto_toll_costs',year] = DA_incremental_toll_costs_inc4_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc1', tm_run_id, metric_id,'intermediate','Commercial','truck_toll_costs',year] = LRG_incremental_toll_costs_inc1_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc2', tm_run_id, metric_id,'intermediate','Commercial','truck_toll_costs',year] = LRG_incremental_toll_costs_inc2_minor_grouping
        metrics_dict[key, 'Toll Costs (2023$)', 'inc3', tm_run_id, metric_id,'intermediate','Commercial','truck_toll_costs',year] = LRG_incremental_toll_costs_inc3_minor_grouping

        metrics_dict[key, 'Toll Costs (2023$)', 'hov', tm_run_id, metric_id,'debug','Houshold','hov_toll_costs',year] = S3_incremental_toll_costs_minor_grouping

        if (DA_incremental_toll_costs_minor_grouping == 0):
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping = 0
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc1 = 0
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc2 = 0
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc3 = 0
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc4 = 0

            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1 = 0
            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc2 = 0
            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc3 = 0
            hov_ratio_time_savings_to_toll_costs_minor_grouping = 0
        else:
            # calculate ratios for overall + inc groups and enter into metrics dict 
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping = priv_auto_travel_time_savings_minor_grouping/DA_incremental_toll_costs_minor_grouping
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc1 = q1_household_travel_time_savings_minor_grouping/DA_incremental_toll_costs_inc1_minor_grouping
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc2 = q2_household_travel_time_savings_minor_grouping/DA_incremental_toll_costs_inc2_minor_grouping
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc3 = q3_household_travel_time_savings_minor_grouping/DA_incremental_toll_costs_inc3_minor_grouping
            priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc4 = q4_household_travel_time_savings_minor_grouping/DA_incremental_toll_costs_inc4_minor_grouping

            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1 = q1_commercial_travel_time_savings_minor_grouping/LRG_incremental_toll_costs_inc1_minor_grouping
            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc2 = q2_commercial_travel_time_savings_minor_grouping/LRG_incremental_toll_costs_inc2_minor_grouping
            comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc3 = q3_commercial_travel_time_savings_minor_grouping/LRG_incremental_toll_costs_inc3_minor_grouping

            hov_ratio_time_savings_to_toll_costs_minor_grouping = priv_auto_travel_time_savings_minor_grouping/S3_incremental_toll_costs_minor_grouping

        if S3_incremental_toll_costs_minor_grouping == 0: #make the ratio 0 if there is no cost to drive
            hov_ratio_time_savings_to_toll_costs_minor_grouping = 0
 

        metrics_dict[key, 'Ratio', 'inc1', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc1
        metrics_dict[key, 'Ratio', 'inc2', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc2
        metrics_dict[key, 'Ratio', 'inc3', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc3
        metrics_dict[key, 'Ratio', 'inc4', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc4

        metrics_dict[key, 'Ratio', 'inc1', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1
        metrics_dict[key, 'Ratio', 'inc2', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc2
        metrics_dict[key, 'Ratio', 'inc3', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc3
        
        # add in metric as $ per minute saved
        metrics_dict[key, 'Ratio', 'inc1', tm_run_id, metric_id,'final','Household','Ratio of toll$ (2023$) to minutes saved',year] = DA_incremental_toll_costs_inc1_minor_grouping / time_savings_minutes
        metrics_dict[key, 'Ratio', 'inc2', tm_run_id, metric_id,'final','Household','Ratio of toll$ (2023$) to minutes saved',year] = DA_incremental_toll_costs_inc2_minor_grouping / time_savings_minutes
        metrics_dict[key, 'Ratio', 'inc3', tm_run_id, metric_id,'final','Household','Ratio of toll$ (2023$) to minutes saved',year] = DA_incremental_toll_costs_inc3_minor_grouping / time_savings_minutes
        metrics_dict[key, 'Ratio', 'inc4', tm_run_id, metric_id,'final','Household','Ratio of toll$ (2023$) to minutes saved',year] = DA_incremental_toll_costs_inc4_minor_grouping / time_savings_minutes

        metrics_dict[key, 'Ratio', 'inc1', tm_run_id, metric_id,'final','Commercial','Ratio of toll$ (2023$) to minutes saved',year] = LRG_incremental_toll_costs_inc1_minor_grouping / time_savings_minutes
        metrics_dict[key, 'Ratio', 'inc2', tm_run_id, metric_id,'final','Commercial','Ratio of toll$ (2023$) to minutes saved',year] = LRG_incremental_toll_costs_inc2_minor_grouping / time_savings_minutes
        metrics_dict[key, 'Ratio', 'inc3', tm_run_id, metric_id,'final','Commercial','Ratio of toll$ (2023$) to minutes saved',year] = LRG_incremental_toll_costs_inc3_minor_grouping / time_savings_minutes


        metrics_dict[key, 'Ratio', grouping3, tm_run_id, metric_id,'final','By Corridor','commercial vehicle',year] = comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1

        # ----sum up the ratio of tolls and time savings across the corridors for weighted average

        # ----calculate average vmt, multiply time savings by it?

        sum_of_weighted_ratio_auto_time_savings_to_toll_costs = sum_of_weighted_ratio_auto_time_savings_to_toll_costs + priv_auto_ratio_time_savings_to_toll_costs_minor_grouping * minor_grouping_vmt
        sum_of_weighted_ratio_truck_time_savings_to_toll_costs = sum_of_weighted_ratio_truck_time_savings_to_toll_costs + comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1 * minor_grouping_vmt
        sum_of_weighted_ratio_hov_time_savings_to_toll_costs = sum_of_weighted_ratio_hov_time_savings_to_toll_costs + hov_ratio_time_savings_to_toll_costs_minor_grouping * minor_grouping_vmt
        sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc1 = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc1 + priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc1 * minor_grouping_vmt
        sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc2 = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc2 + priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc2 * minor_grouping_vmt
        sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc3 = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc3 + priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc3 * minor_grouping_vmt
        sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc4 = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc4 + priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc4 * minor_grouping_vmt
        sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc1 = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc1 + comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1 * minor_grouping_vmt
        sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc2 = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc2 + comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc2 * minor_grouping_vmt
        sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc3 = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc3 + comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc3 * minor_grouping_vmt

        sum_of_ratio_auto_time_savings_to_toll_costs += priv_auto_ratio_time_savings_to_toll_costs_minor_grouping
        sum_of_ratio_auto_time_savings_to_toll_costs_inc1 += priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc1
        sum_of_ratio_auto_time_savings_to_toll_costs_inc2 += priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc2
        sum_of_ratio_auto_time_savings_to_toll_costs_inc3 += priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc3
        sum_of_ratio_auto_time_savings_to_toll_costs_inc4 += priv_auto_ratio_time_savings_to_toll_costs_minor_grouping_inc4
        sum_of_ratio_truck_time_savings_to_toll_costs_inc1 += comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc1
        sum_of_ratio_truck_time_savings_to_toll_costs_inc2 += comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc2
        sum_of_ratio_truck_time_savings_to_toll_costs_inc3 += comm_veh_ratio_time_savings_to_toll_costs_minor_grouping_inc3
        sum_of_ratio_hov_time_savings_to_toll_costs += hov_ratio_time_savings_to_toll_costs_minor_grouping

        #----sum of weights (vmt of corridor) to be used for weighted average
        sum_of_weights = sum_of_weights + minor_grouping_vmt
        # for corrdior simple average calc
        n = n+1

    # ----commented out to clear clutter. use for debugging
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'intermediate','Private Auto','sum_of_ratio_auto_time_savings_to_toll_costs_weighted_by_vmt',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'intermediate','Private Auto','sum_of_ratio_auto_time_savings_to_toll_costs_inc1_weighted_by_vmt',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc1
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'intermediate','Private Auto','sum_of_ratio_auto_time_savings_to_toll_costs_inc2_weighted_by_vmt',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc2
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'intermediate','Commercial Vehicle','sum_of_ratio_truck_time_savings_to_toll_costs_weighted_by_vmt',year] = sum_of_weighted_ratio_truck_time_savings_to_toll_costs
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'intermediate','High Occupancy Vehicle','sum_of_ratio_hov_time_savings_to_toll_costs_weighted_by_vmt',year] = sum_of_weighted_ratio_hov_time_savings_to_toll_costs

    # weighted averages
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc1', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc1/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc2', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc2/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc3', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc3/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc4', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_auto_time_savings_to_toll_costs_inc4/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc1', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc1/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc2', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc2/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', 'inc3', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_truck_time_savings_to_toll_costs_inc3/sum_of_weights
    metrics_dict['Weighted Average Across Tolled Corridors', 'Ratio', grouping3, tm_run_id, metric_id,'debug','High Occupancy Vehicle','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_weighted_ratio_hov_time_savings_to_toll_costs/sum_of_weights

    # simple averages
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc1', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_auto_time_savings_to_toll_costs_inc1/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc2', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_auto_time_savings_to_toll_costs_inc2/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc3', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_auto_time_savings_to_toll_costs_inc3/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc4', tm_run_id, metric_id,'final','Household','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_auto_time_savings_to_toll_costs_inc4/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc1', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_truck_time_savings_to_toll_costs_inc1/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc2', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_truck_time_savings_to_toll_costs_inc2/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', 'inc3', tm_run_id, metric_id,'final','Commercial','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_truck_time_savings_to_toll_costs_inc3/n
    metrics_dict['Simple Average Across Tolled Corridors', 'Ratio', grouping3, tm_run_id, metric_id,'debug','High Occupancy Vehicle','Ratio of Monetary value of travel time savings to toll costs',year] = sum_of_ratio_hov_time_savings_to_toll_costs/n


def return_E1_DF(tm_run_id, od_df, All_or_EPC):
    # change orig_CITY to 'All TAZs

    od_df['orig_CITY'] = All_or_EPC + ' TAZs'

    # we're going to aggregate trip modes; auto includes TAXI and TNC    
    od_df['agg_trip_mode'] = "N/A"
    od_df.loc[ od_df.trip_mode.isin(MODES_TRANSIT),      'agg_trip_mode' ] = "transit"
    od_df.loc[ od_df.trip_mode.isin(MODES_PRIVATE_AUTO), 'agg_trip_mode' ] = "auto"
    od_df.loc[ od_df.trip_mode.isin(MODES_TAXI_TNC),     'agg_trip_mode' ] = "auto"

    # to get weighted average, transform to total travel time
    od_df['tot_travel_time_in_mins'] = \
        od_df['avg_travel_time_in_mins']*od_df['num_trips']

    # pivot down to orig_CITY x dest_CITY x agg_trip_mode
    od_df = pd.pivot_table(od_df, 
                                             index=['orig_CITY','dest_CITY','agg_trip_mode'],
                                             values=['num_trips','tot_travel_time_in_mins'],
                                             aggfunc={'num_trips':numpy.sum, 'tot_travel_time_in_mins':numpy.sum})
    od_df.reset_index(inplace=True)
    od_df['avg_travel_time_in_mins'] = \
        od_df['tot_travel_time_in_mins']/od_df['num_trips']
    # LOGGER.debug(od_df)

    # pivot again to move agg_mode to column
    # columns will now be: orig_CITY_, dest_CITY_, avg_travel_time_in_mins_auto, avg_travel_time_in_mins_transit, num_trips_auto, num_trips_transit
    od_df = pd.pivot_table(od_df, 
                                             index=['orig_CITY','dest_CITY'],
                                             columns=['agg_trip_mode'],
                                             values=['num_trips','avg_travel_time_in_mins'])
    od_df.reset_index(inplace=True)
    # flatten resulting MultiIndex column names
    # rename from ('orig_CITY',''), ('dest_CITY',''), ('avg_travel_time_in_mins','auto'), ('avg_travel_time_in_mins', 'transit'), ...
    # to orig_CITY, dest_CITY, avg_travel_time_in_mins_auto, avg_travel_time_in_mins_transit, ...
    od_df.columns = ['_'.join(col) if len(col[1]) > 0 else col[0] for col in od_df.columns.values]

    # add ratio
    od_df['ratio_travel_time_transit_auto'] = \
        od_df['avg_travel_time_in_mins_transit']/od_df['avg_travel_time_in_mins_auto']
    
    # note that this does not include NaNs in either the numerator or the denominator, which I think is correct
    # TODO: in the previous implementation, NaN is converted to zero, which artificially lowers the average.
    # for example, if most ODs had NO transit paths, then the average ratio would be very low, making it seem like transit travel times
    # compare favorably to auto, which they do not
    average_ratio = od_df['ratio_travel_time_transit_auto'].mean()
    LOGGER.info("  => average_ratio={}".format(average_ratio))
    # LOGGER.debug(od_df)

    # convert to metrics dataframe by pivoting one last time to just columns orig_CITY, dest_CITY
    od_df = pd.melt(od_df, 
                                      id_vars=['orig_CITY','dest_CITY'], 
                                      var_name='metric_desc',
                                      value_name='value')
    # travel times and num trips are extra
    od_df['intermediate/final']   = 'extra'
    # ratios are intermediate
    od_df.loc[ od_df.metric_desc.str.startswith('ratio'), 'intermediate/final'] = 'intermediate'

    # key is orig_CITY, dest_CITY
    od_df['key']  = od_df['orig_CITY'] + "_" + od_df['dest_CITY']
    od_df.drop(columns=['orig_CITY','dest_CITY'], inplace=True)

    od_df['modelrun_id'] = tm_run_id
    od_df['year'] = tm_run_id[:4]
    od_df['metric_id'] = 'Efficient 1'
    # LOGGER.info(od_df)
    return od_df

def calculate_Efficient1_ratio_travel_time(tm_run_id: str) -> pd.DataFrame:
    """ Calculates Efficient1: Ratio of travel time by transit over that of auto between representative origin-destination pairs
    
    Args:
        tm_run_id (str): Travel model run ID

    Returns:
        pandas.DataFrame: with columns a subset of METRICS_COLUMNS, including 
          metric_id   = 'Efficient 1'
          modelrun_id = tm_run_id
        Metrics returned:
          key                       intermediate/final  metric_desc
          [origCITY_destCITY]       extra               avg_travel_time_in_mins_auto
          [origCITY_destCITY]       extra               avg_travel_time_in_mins_transit
          [origCITY_destCITY]       extra               num_trips_auto
          [origCITY_destCITY]       extra               num_trips_transit
          [origCITY_destCITY]       intermediate        ratio_travel_time_transit_auto
          Average across OD pairs   final               ratio_travel_time_transit_auto_across_pairs

    Notes:
    * Representative origin-destination pairs are given by TAZs corresponding with 
      NGFS_OD_CITIES_FILE and NGFS_OD_CITIES_OF_INTEREST
    * Auto modes includes taxi and tncs
    * Final calculation is the average of these ratios (not weighted) across all OD pairs,
      excluding those which have no transit trips and therefore lack a transit travel time
    
      TODO: Does this make sense?  If a market is very small, should it be considered equally
    """
    METRIC_ID = 'Efficient 1'
    LOGGER.info("Calculating {} for {}".format(METRIC_ID, tm_run_id))
    
    # columns: orig_taz, dest_taz, trip_mode, timeperiod_label, incQ, incQ_label, num_trips, avg_travel_time_in_mins
    ODTravelTime_byModeTimeperiod_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "core_summaries", ODTRAVELTIME_FILENAME) #changed "ODTravelTime_byModeTimeperiodIncome.csv" to a variable for better performance during debugging
    # this is large so join/subset it immediately
    trips_od_travel_time_df = pd.read_csv(ODTravelTime_byModeTimeperiod_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(trips_od_travel_time_df), ODTravelTime_byModeTimeperiod_file))

    trips_od_travel_time_df = trips_od_travel_time_df.loc[ trips_od_travel_time_df.timeperiod_label == 'AM Peak' ]
    LOGGER.info("  Filtered to AM only: {:,} rows".format(len(trips_od_travel_time_df)))

    # pivot out the income since we don't need it
    trips_od_travel_time_df = pd.pivot_table(trips_od_travel_time_df,
                                             index=['orig_taz','dest_taz','trip_mode'],
                                             values=['num_trips','avg_travel_time_in_mins'],
                                             aggfunc={'num_trips':numpy.sum, 'avg_travel_time_in_mins':numpy.mean})
    trips_od_travel_time_df.reset_index(inplace=True)
    LOGGER.info("  Aggregated income groups: {:,} rows".format(len(trips_od_travel_time_df)))

    # join to OD cities for origin
    trips_od_travel_time_df = pd.merge(left=trips_od_travel_time_df,
                                       right=NGFS_OD_CITIES_DF,
                                       left_on="orig_taz",
                                       right_on="taz1454")
    trips_od_travel_time_df.rename(columns={"CITY":"orig_CITY"}, inplace=True)
    trips_od_travel_time_df.drop(columns=["taz1454"], inplace=True)
    # join to OD cities for destination
    trips_od_travel_time_df = pd.merge(left=trips_od_travel_time_df,
                                       right=NGFS_OD_CITIES_DF,
                                       left_on="dest_taz",
                                       right_on="taz1454")
    trips_od_travel_time_df.rename(columns={"CITY":"dest_CITY"}, inplace=True)
    trips_od_travel_time_df.drop(columns=["taz1454"], inplace=True)
    LOGGER.info("  Joined with {} for origin, destination: {:,} rows".format(NGFS_OD_CITIES_FILE, len(trips_od_travel_time_df)))

    # filter a copy to only those ending in cities of interest
    trips_ending_in_city_dt_od_travel_time_df = trips_od_travel_time_df.copy().loc[(trips_od_travel_time_df['dest_CITY'] == 'SAN FRANCISCO')|
                                                                                    (trips_od_travel_time_df['dest_CITY'] == 'OAKLAND')|
                                                                                    (trips_od_travel_time_df['dest_CITY'] == 'SAN JOSE')]
    # join to epc lookup table
    trips_ending_in_city_dt_od_travel_time_df = pd.merge(left=trips_ending_in_city_dt_od_travel_time_df,
                                                        right=NGFS_EPC_TAZ_DF,
                                                        left_on="orig_taz",
                                                        right_on="TAZ1454")
    # filter a copy to only those staring in EPCs
    trips_starting_EPC_ending_in_city_dt_od_travel_time_df = trips_ending_in_city_dt_od_travel_time_df.copy().loc[(trips_ending_in_city_dt_od_travel_time_df['taz_epc'] == 1)]

    # filter again to only those of interest
    trips_od_travel_time_df = pd.merge(left=trips_od_travel_time_df,
                                       right=NGFS_OD_CITIES_OF_INTEREST_DF,
                                       indicator=True)
    trips_od_travel_time_df = trips_od_travel_time_df.loc[ trips_od_travel_time_df._merge == 'both']
    LOGGER.info("  Filtered to only NGFS_OD_CITIES_OF_INTEREST: {:,} rows".format(len(trips_od_travel_time_df)))

    # we're going to aggregate trip modes; auto includes TAXI and TNC
    trips_od_travel_time_df['agg_trip_mode'] = "N/A"
    trips_od_travel_time_df.loc[ trips_od_travel_time_df.trip_mode.isin(MODES_TRANSIT),      'agg_trip_mode' ] = "transit"
    trips_od_travel_time_df.loc[ trips_od_travel_time_df.trip_mode.isin(MODES_PRIVATE_AUTO), 'agg_trip_mode' ] = "auto"
    trips_od_travel_time_df.loc[ trips_od_travel_time_df.trip_mode.isin(MODES_TAXI_TNC),     'agg_trip_mode' ] = "auto"

    # to get weighted average, transform to total travel time
    trips_od_travel_time_df['tot_travel_time_in_mins'] = \
        trips_od_travel_time_df['avg_travel_time_in_mins']*trips_od_travel_time_df['num_trips']

    # pivot down to orig_CITY x dest_CITY x agg_trip_mode
    trips_od_travel_time_df = pd.pivot_table(trips_od_travel_time_df, 
                                             index=['orig_CITY','dest_CITY','agg_trip_mode'],
                                             values=['num_trips','tot_travel_time_in_mins'],
                                             aggfunc={'num_trips':numpy.sum, 'tot_travel_time_in_mins':numpy.sum})
    trips_od_travel_time_df.reset_index(inplace=True)
    trips_od_travel_time_df['avg_travel_time_in_mins'] = \
        trips_od_travel_time_df['tot_travel_time_in_mins']/trips_od_travel_time_df['num_trips']
    # LOGGER.debug(trips_od_travel_time_df)

    # pivot again to move agg_mode to column
    # columns will now be: orig_CITY_, dest_CITY_, avg_travel_time_in_mins_auto, avg_travel_time_in_mins_transit, num_trips_auto, num_trips_transit
    trips_od_travel_time_df = pd.pivot_table(trips_od_travel_time_df, 
                                             index=['orig_CITY','dest_CITY'],
                                             columns=['agg_trip_mode'],
                                             values=['num_trips','avg_travel_time_in_mins'])
    trips_od_travel_time_df.reset_index(inplace=True)
    # flatten resulting MultiIndex column names
    # rename from ('orig_CITY',''), ('dest_CITY',''), ('avg_travel_time_in_mins','auto'), ('avg_travel_time_in_mins', 'transit'), ...
    # to orig_CITY, dest_CITY, avg_travel_time_in_mins_auto, avg_travel_time_in_mins_transit, ...
    trips_od_travel_time_df.columns = ['_'.join(col) if len(col[1]) > 0 else col[0] for col in trips_od_travel_time_df.columns.values]

    # add ratio
    trips_od_travel_time_df['ratio_travel_time_transit_auto'] = \
        trips_od_travel_time_df['avg_travel_time_in_mins_transit']/trips_od_travel_time_df['avg_travel_time_in_mins_auto']
    
    # note that this does not include NaNs in either the numerator or the denominator, which I think is correct
    # TODO: in the previous implementation, NaN is converted to zero, which artificially lowers the average.
    # for example, if most ODs had NO transit paths, then the average ratio would be very low, making it seem like transit travel times
    # compare favorably to auto, which they do not
    average_ratio = trips_od_travel_time_df['ratio_travel_time_transit_auto'].mean()
    LOGGER.info("  => average_ratio={}".format(average_ratio))
    # LOGGER.debug(trips_od_travel_time_df)

    # convert to metrics dataframe by pivoting one last time to just columns orig_CITY, dest_CITY
    trips_od_travel_time_df = pd.melt(trips_od_travel_time_df, 
                                      id_vars=['orig_CITY','dest_CITY'], 
                                      var_name='metric_desc',
                                      value_name='value')
    # travel times and num trips are extra
    trips_od_travel_time_df['intermediate/final']   = 'extra'
    # ratios are intermediate
    trips_od_travel_time_df.loc[ trips_od_travel_time_df.metric_desc.str.startswith('ratio'), 'intermediate/final'] = 'intermediate'

    # key is orig_CITY, dest_CITY
    trips_od_travel_time_df['key']  = trips_od_travel_time_df['orig_CITY'] + "_" + trips_od_travel_time_df['dest_CITY']
    trips_od_travel_time_df.drop(columns=['orig_CITY','dest_CITY'], inplace=True)

    trips_od_travel_time_df['modelrun_id'] = tm_run_id
    trips_od_travel_time_df['year'] = tm_run_id[:4]
    trips_od_travel_time_df['metric_id'] = METRIC_ID
    # LOGGER.info(trips_od_travel_time_df)
    
    # finally, add the average_ratio
    final_row = pd.DataFrame.from_records([{
        'modelrun_id':          tm_run_id,
        'metric_id':            METRIC_ID,
        'intermediate/final':   "final",
        'key':                  "Average across OD pairs",
        'metric_desc':          "ratio_travel_time_transit_auto_across_pairs",
        'year':                 tm_run_id[:4], 
        'value':                average_ratio
     }])
    # LOGGER.debug(final_row)

    # all TAZ rows
    all_taz_rows = return_E1_DF(tm_run_id, trips_ending_in_city_dt_od_travel_time_df, 'All')
    epc_taz_rows = return_E1_DF(tm_run_id, trips_starting_EPC_ending_in_city_dt_od_travel_time_df, 'EPC')
     
    trips_od_travel_time_df = pd.concat([trips_od_travel_time_df, final_row, all_taz_rows, epc_taz_rows])
    LOGGER.debug("{} Result: \n{}".format(METRIC_ID, trips_od_travel_time_df))
    return trips_od_travel_time_df

def calculate_Efficient2_commute_mode_share(tm_run_id: str) -> pd.DataFrame:
    """ Calculates Efficient 2: Transit, walk, bike and telecommute mode share of commute *tours*

    Args:
        tm_run_id (str): Travel model run ID

    Returns:
        pandas.DataFrame: Dataframe with columns a subset of METRICS_COLUMNS, including 
          metric_id   = 'Efficient 2'
          modelrun_id = tm_run_id
        Metrics returned:
          key             intermediate/final  metric_desc
          [commute_mode]  intermediate        commute_tours
          [commute_mode]  final               commute_tours_share

        where commute_mode is one of:
         SOV, HOV, transit, walk, bike, telecommute, taxi/TNC
    """
    # from model-files\scripts\preprocess\updateTelecommuteConstants.py
    # see EN7 Telecommuting.xlsx (https://mtcdrive.box.com/s/uw3n8wyervle6r2cgoz1j6k4i5lmv253)
    # for 2015 and before
    P_notworking_if_noworktour_FT = 0.560554289
    P_notworking_if_noworktour_PT = 0.553307383
    # future
    P_notworking_FT = 0.107904288
    P_notworking_PT = 0.205942146

    METRIC_ID = 'Efficient 2'
    LOGGER.info("Calculating {} for {}".format(METRIC_ID, tm_run_id))

    journey_to_work_modes_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "core_summaries", "JourneyToWork_modes.csv")
    tm_journey_to_work_df = pd.read_csv(journey_to_work_modes_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_journey_to_work_df), journey_to_work_modes_file))
    LOGGER.debug("tm_journey_to_work_df.head() =\n{}".format(tm_journey_to_work_df.head()))

    # create aggregate mode
    tm_journey_to_work_df['commute_mode'] = 'Unknown'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_SOV),      'commute_mode'] = 'SOV'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_HOV),      'commute_mode'] = 'HOV'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_TRANSIT),  'commute_mode'] = 'transit'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_TAXI_TNC), 'commute_mode'] = 'taxi/TNC'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_WALK),     'commute_mode'] = 'walk'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode.isin(MODES_BIKE),     'commute_mode'] = 'bike'
    tm_journey_to_work_df.loc[ tm_journey_to_work_df.tour_mode == 0,                 'commute_mode'] = 'did not go to work'

    # aggregate to person types and move person types to columns
    tm_journey_to_work_df = tm_journey_to_work_df.groupby(['ptype_label', 'commute_mode']).agg(
        {"freq": "sum"}).reset_index()
    tm_journey_to_work_df = tm_journey_to_work_df.pivot(index=['commute_mode'],
                                                        columns=['ptype_label'])
    # reset multiindex columns to just a single level, ptype_label
    tm_journey_to_work_df.columns = tm_journey_to_work_df.columns.get_level_values(1)
    # reorder to match tableau convention
    tm_journey_to_work_df = tm_journey_to_work_df[[
        'Full-time worker',
        'Part-time worker',
        'College student',
        'Driving-age student']]
    # add row for total workers
    tm_journey_to_work_df.loc['all_modes incl time off'] = tm_journey_to_work_df.sum(axis=0)

    # add row for telecommute, not-working, start with 0 for the four person types
    LOGGER.debug("tm_journey_to_work_df:\n{}".format(tm_journey_to_work_df))
    tm_journey_to_work_df.loc['telecommute'] = [0,0,0,0]
    tm_journey_to_work_df.loc['time off'] = [0,0,0,0]

    # calculate non-workers consistently with model-files\scripts\preprocess\updateTelecommuteConstants.py
    model_year = int(tm_run_id[:4])
    # note: Full-time worker and Part-time worker columns will be float now
    if model_year <= 2020:
        tm_journey_to_work_df.at['time off', 'Full-time worker'] = P_notworking_if_noworktour_FT*tm_journey_to_work_df.at['did not go to work', 'Full-time worker']
        tm_journey_to_work_df.at['time off', 'Part-time worker'] = P_notworking_if_noworktour_PT*tm_journey_to_work_df.at['did not go to work', 'Part-time worker']
    else:
        tm_journey_to_work_df.at['time off', 'Full-time worker'] = P_notworking_FT*tm_journey_to_work_df.at['all_modes incl time off', 'Full-time worker']
        tm_journey_to_work_df.at['time off', 'Part-time worker'] = P_notworking_PT*tm_journey_to_work_df.at['all_modes incl time off', 'Part-time worker']
    # assume no telecommute for driving-age students and college students
    tm_journey_to_work_df.at['time off', 'Driving-age student'] = tm_journey_to_work_df.at['did not go to work' , 'Driving-age student']
    tm_journey_to_work_df.at['time off', 'College student']     = tm_journey_to_work_df.at['did not go to work' , 'College student']
    # subtract for telecommute
    tm_journey_to_work_df.loc['telecommute'] = tm_journey_to_work_df.loc['did not go to work'] - tm_journey_to_work_df.loc['time off']

    # create all_modes excl time off
    tm_journey_to_work_df.loc['all_modes excl time off'] = tm_journey_to_work_df.loc['all_modes incl time off'] - tm_journey_to_work_df.loc['time off']

    # add column for all person types
    tm_journey_to_work_df['All workers'] = tm_journey_to_work_df.sum(axis=1)
    LOGGER.debug("tm_journey_to_work_df:\n{}".format(tm_journey_to_work_df))
    LOGGER.debug(tm_journey_to_work_df.columns)
    LOGGER.debug(tm_journey_to_work_df.index)
    # drop did not go to work since it's covered by telecommute + time off
    tm_journey_to_work_df = tm_journey_to_work_df.loc[ tm_journey_to_work_df.index != 'did not go to work']
    # drop time off and all modes incl time off since the mode share won't include those
    tm_journey_to_work_df = tm_journey_to_work_df.loc[ tm_journey_to_work_df.index != 'time off']
    tm_journey_to_work_df = tm_journey_to_work_df.loc[ tm_journey_to_work_df.index != 'all_modes incl time off']

    # convert to shares
    tm_journey_to_work_shares_df = tm_journey_to_work_df/tm_journey_to_work_df.loc['all_modes excl time off']
    LOGGER.debug("tm_journey_to_work_shares_df:\n{}".format(tm_journey_to_work_shares_df))

    # reformat to metrics
    # we only care about the All Workers column
    tm_journey_to_work_df = tm_journey_to_work_df[['All workers']].reset_index(drop=False)
    tm_journey_to_work_df['intermediate/final'] = 'intermediate'
    tm_journey_to_work_df['metric_desc'] = 'commute_tours'
    # LOGGER.debug("tm_journey_to_work_df:\n{}".format(tm_journey_to_work_df))
    # columns: commute_mode, All workers, intermediate/final, metric_desc

    tm_journey_to_work_shares_df = tm_journey_to_work_shares_df[['All workers']].reset_index(drop=False)
    tm_journey_to_work_shares_df['intermediate/final'] = 'final'
    tm_journey_to_work_shares_df['metric_desc'] = 'commute_tours_share'
    # LOGGER.debug("tm_journey_to_work_shares_df:\n{}".format(tm_journey_to_work_shares_df))
    # columns: commute_mode, All workers, intermediate/final, metric_desc

    metrics_df = pd.concat([tm_journey_to_work_df, tm_journey_to_work_shares_df])
    metrics_df.rename(columns={'All workers':'value', 'commute_mode':'key'}, inplace=True)
    # add 2b metric for modeshare by trips for non commute trips
    metrics_df = pd.concat([metrics_df, trips_commute_mode_pkop(tm_run_id, 'Efficient 2b')])
    metrics_df['metric_id'] = METRIC_ID
    metrics_df['modelrun_id'] = tm_run_id
    metrics_df.columns.name = None # it was named ptype_label
    LOGGER.debug("metrics_df:\n{}".format(metrics_df))
    return metrics_df







def sum_grouping(network_df,period): #sum congested time across selected toll class groupings
    return network_df['ctim'+period].sum()




def calculate_travel_time_and_return_weighted_sum_across_corridors(tm_run_id, year, tm_loaded_network_df, metrics_dict):
  # Keeping essential columns of loaded highway network: node A and B, distance, free flow time, congested time
  metric_id = 'Reliable 1'
  grouping1 = ' '
  grouping2 = ' '
  grouping3 = ' '

  # load network_links_TAZ.csv as lookup df to use for equity metric 

  tm_ab_ctim_df = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['USEAM'] == 1)&(tm_loaded_network_df['ft'] != 6)]
  tm_ab_ctim_df = tm_ab_ctim_df.copy()[['Grouping minor_AMPM','a_b','fft','ctimAM','ctimPM', 'distance','volEA_tot', 'volAM_tot', 'volMD_tot', 'volPM_tot', 'volEV_tot']]  

  # create df for parallel arterials  
  tm_parallel_arterials_df = tm_loaded_network_df.copy().merge(parallel_arterials_links, on='a_b', how='left')

  #  calcuate average across corridors
  sum_of_weights = 0 #sum of weights (vmt of corridor) to be used for weighted average 
  total_weighted_travel_time = 0 #sum for numerator
  n = 0 #counter for simple average 
  total_travel_time = 0 #numerator for simple average 

  # for parallel arterials
  sum_of_weights_parallel_arterial = 0 #sum of weights (vmt of corridor) to be used for weighted average 
  total_weighted_travel_time_parallel_arterial = 0 #sum for numerator
  total_travel_time_parallel_arterial = 0 #numerator for simple average 

  for i in minor_groups:
    #     add minor ampm ctim to metric dict
    minor_group_am_df = tm_ab_ctim_df.copy().loc[tm_ab_ctim_df['Grouping minor_AMPM'] == i+'_AM']
    minor_group_pm_df = tm_ab_ctim_df.copy().loc[tm_ab_ctim_df['Grouping minor_AMPM'] == i+'_PM']
    minor_group_am = sum_grouping(minor_group_am_df,'AM')
    minor_group_pm = sum_grouping(minor_group_pm_df,'PM')

    # add in extra metric for length of grouping
    length_of_grouping = (minor_group_am_df['distance']).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'%s' % i + '_AM_length',year] = length_of_grouping
    length_of_grouping = (minor_group_pm_df['distance']).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'%s' % i + '_PM_length',year] = length_of_grouping


    # for parallel arterials
    minor_group_am_parallel_arterial_df = tm_parallel_arterials_df.copy().loc[(tm_parallel_arterials_df['Parallel_Corridor'].str.contains(i+'_AM') == True)]
    minor_group_pm_parallel_arterial_df = tm_parallel_arterials_df.copy().loc[(tm_parallel_arterials_df['Parallel_Corridor'].str.contains(i+'_PM') == True)]
    minor_group_am_parallel_arterial = sum_grouping(minor_group_am_parallel_arterial_df,'AM')
    minor_group_pm_parallel_arterial = sum_grouping(minor_group_pm_parallel_arterial_df,'PM')

    # add in extra metric for length of grouping (parallel arterials)
    length_of_grouping = (minor_group_am_parallel_arterial_df['distance']).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'%s' % i + '_AM_parallel_arterial_length',year] = length_of_grouping
    length_of_grouping = (minor_group_pm_parallel_arterial_df['distance']).sum()
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'%s' % i + '_PM_parallel_arterial_length',year] = length_of_grouping

    # vmt to be used for weighted averages
    index_a_b = minor_group_am_df.copy()[['a_b']]
    network_for_vmt_df_AM = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
    index_a_b = minor_group_pm_df.copy()[['a_b']]
    network_for_vmt_df_PM = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
    vmt_minor_grouping_AM = network_for_vmt_df_AM['volAM_tot'].sum()
    vmt_minor_grouping_PM = network_for_vmt_df_PM['volPM_tot'].sum()
    # will use avg vmt for simplicity
    am_pm_avg_vmt = numpy.mean([vmt_minor_grouping_AM,vmt_minor_grouping_PM])

    # [for parallel arterials] vmt to be used for weighted averages
    index_a_b = minor_group_am_parallel_arterial_df.copy()[['a_b']]
    network_for_vmt_df_AM_parallel_arterial = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
    index_a_b = minor_group_am_parallel_arterial_df.copy()[['a_b']]
    network_for_vmt_df_PM_parallel_arterial = tm_loaded_network_df_base.copy().merge(index_a_b, on='a_b', how='right')
    vmt_minor_grouping_AM_parallel_arterial = network_for_vmt_df_AM_parallel_arterial['volAM_tot'].sum()
    vmt_minor_grouping_PM_parallel_arterial = network_for_vmt_df_PM_parallel_arterial['volPM_tot'].sum()
    # will use avg vmt for simplicity
    am_pm_avg_vmt_parallel_arterial = numpy.mean([vmt_minor_grouping_AM_parallel_arterial,vmt_minor_grouping_PM_parallel_arterial])

    # __commented out to reduce clutter - not insightful - can reveal for debugging
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'%s' % i + '_AM_vmt',year] = vmt_minor_grouping_AM
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'%s' % i + '_PM_vmt',year] = vmt_minor_grouping_PM

    # add free flow time column for comparison
    # note: the base run overrides the comparison run - tableau will show the base run fft
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'extra',i,'Freeway_travel_time_%s' % i + '_AM',year] = minor_group_am_df['fft'].sum()
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'extra',i,'Freeway_travel_time_%s' % i + '_PM',year] = minor_group_pm_df['fft'].sum()
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'extra',i,'Parallel_Arterial_travel_time_%s' % i + '_AM',year] = minor_group_am_parallel_arterial_df['fft'].sum()
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'extra',i,'Parallel_Arterial_travel_time_%s' % i + '_PM',year] = minor_group_pm_parallel_arterial_df['fft'].sum()
    # add average fft for each minor grouping to metric dict
    avgfft_minor_group = numpy.mean([minor_group_am_df['fft'].sum(),minor_group_pm_df['fft'].sum()])
    avgfft_parallel_arterial = numpy.mean([minor_group_am_parallel_arterial_df['fft'].sum(),minor_group_pm_parallel_arterial_df['fft'].sum()])
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'intermediate',i,'Freeway_avg_travel_time_%s' % i,year] = avgfft_minor_group
    metrics_dict[grouping1, grouping2, grouping3, 'FFT',metric_id,'intermediate',i,'Parallel_Arterial_avg_travel_time_%s' % i,year] = avgfft_parallel_arterial

    # add travel times to metric dict
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Freeway_travel_time_%s' % i + '_AM',year] = minor_group_am
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Freeway_travel_time_%s' % i + '_PM',year] = minor_group_pm
    # weighted AM,PM travel times (by vmt)
    weighted_AM_travel_time_by_vmt = minor_group_am * am_pm_avg_vmt
    weighted_PM_travel_time_by_vmt = minor_group_pm * am_pm_avg_vmt

    # [for parallel arterials] add travel times to metric dict
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Parallel_Arterial_travel_time_%s' % i + '_AM',year] = minor_group_am_parallel_arterial
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Parallel_Arterial_travel_time_%s' % i + '_PM',year] = minor_group_pm_parallel_arterial
    # [for parallel arterials] weighted AM,PM travel times (by vmt)
    weighted_AM_travel_time_by_vmt_parallel_arterial = minor_group_am_parallel_arterial * am_pm_avg_vmt_parallel_arterial
    weighted_PM_travel_time_by_vmt_parallel_arterial = minor_group_pm_parallel_arterial * am_pm_avg_vmt_parallel_arterial

    # __commented out to reduce clutter - not insightful - can reveal for debugging
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'travel_time_%s' % i + '_AM_weighted_by_vmt',year] = weighted_AM_travel_time_by_vmt
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'travel_time_%s' % i + '_PM_weighted_by_vmt',year] = weighted_PM_travel_time_by_vmt

    #     add average ctim for each minor grouping to metric dict
    avgtime = numpy.mean([minor_group_am,minor_group_pm])
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'Freeway_avg_travel_time_%s' % i,year] = avgtime
    avgtime_weighted_by_vmt = numpy.mean([weighted_AM_travel_time_by_vmt,weighted_PM_travel_time_by_vmt])

    # [for parallel arterials] add average ctim for each minor grouping to metric dict
    avgtime_parallel_arterial = numpy.mean([minor_group_am_parallel_arterial,minor_group_pm_parallel_arterial])
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'Parallel_Arterial_avg_travel_time_%s' % i,year] = avgtime_parallel_arterial
    avgtime_weighted_by_vmt_parallel_arterial = numpy.mean([weighted_AM_travel_time_by_vmt_parallel_arterial,weighted_PM_travel_time_by_vmt_parallel_arterial])

    # __commented out to reduce clutter - not insightful - can reveal for debugging
    # metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'final',i,'avg_travel_time_%s_weighted_by_vmt' % i,year] = avgtime_weighted_by_vmt

    # if 'Path2' in tm_run_id: # investigation: compare travel time changes on all parallel tolled arterials
    # TODO: review method for comparing pathways with no tolled arterials (ex: base run)
    # create df for tolled parallel arterial links (using pathway 2 network toll classes and TOLLCLASS_Designations.xlsx as lookup)
    tm_tolled_arterial_links_df = pd.merge(left=tm_loaded_network_df.copy(), right=NGFS_PATHWAY2_TOLLED_ARTERIALS_FILE, how='left', left_on=['a','b'], right_on=['a','b'])
    tm_tolled_arterial_links_df['tollclass'] = tm_tolled_arterial_links_df['tollclass_y']
    tm_tolled_arterial_links_df.drop(columns=["tollclass_x"], inplace=True)
    tm_tolled_arterial_links_df.drop(columns=["tollclass_y"], inplace=True)
    tm_tolled_arterial_links_df = tm_tolled_arterial_links_df.copy().merge(TOLLCLASS_LOOKUP_DF, on='tollclass', how='left').loc[(tm_tolled_arterial_links_df['ft'] == 3)|(tm_tolled_arterial_links_df['ft'] == 4)|(tm_tolled_arterial_links_df['ft'] == 7)]

    # for tolled arterial links
    # TODO: remove duplicate 'Grouping minor' column
    minor_group_am_tolled_arterial_df = tm_tolled_arterial_links_df.copy().loc[(tm_tolled_arterial_links_df['Grouping minor_y'].str.contains(i+'_AM') == True)]
    minor_group_pm_tolled_arterial_df = tm_tolled_arterial_links_df.copy().loc[(tm_tolled_arterial_links_df['Grouping minor_y'].str.contains(i+'_PM') == True)]
    minor_group_am_tolled_arterial = sum_grouping(minor_group_am_tolled_arterial_df,'AM')
    minor_group_pm_tolled_arterial = sum_grouping(minor_group_pm_tolled_arterial_df,'PM')

    # add travel times for all tolled arterials to metrics dict
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Tolled_Arterial_travel_time_%s' % i + '_AM',year] = minor_group_am_tolled_arterial
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra',i,'Tolled_Arterial_travel_time_%s' % i + '_PM',year] = minor_group_pm_tolled_arterial

    # [for tolled arterials] add average ctim for each minor grouping to metric dict
    avgtime_tolled_arterial = numpy.mean([minor_group_am_tolled_arterial,minor_group_pm_tolled_arterial])
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate',i,'Tolled_Arterial_avg_travel_time_%s' % i,year] = avgtime_tolled_arterial
    
    # for corrdior average calc
    sum_of_weights = sum_of_weights + am_pm_avg_vmt
    total_weighted_travel_time = total_weighted_travel_time + (avgtime_weighted_by_vmt)
    n += 1
    total_travel_time += avgtime

    # [for parallel arterials]
    sum_of_weights_parallel_arterial += am_pm_avg_vmt_parallel_arterial
    total_weighted_travel_time_parallel_arterial += avgtime_weighted_by_vmt_parallel_arterial
    total_travel_time_parallel_arterial += avgtime_parallel_arterial

  return [sum_of_weights, total_weighted_travel_time, n, total_travel_time, sum_of_weights_parallel_arterial, total_weighted_travel_time_parallel_arterial, total_travel_time_parallel_arterial]




def calculate_Reliable1_change_travel_time(tm_run_id, year, tm_loaded_network_df, metrics_dict):    
    # 5) Change in peak hour travel time on key freeway corridors and parallel arterials

    # borrowed from pba metrics calculate_Connected2_hwy_traveltimes()
    metric_id = 'Reliable 1'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '

    # calculate travel times on each cprridor for both runs
    this_run_metric = calculate_travel_time_and_return_weighted_sum_across_corridors(tm_run_id, year, tm_loaded_network_df, metrics_dict)
    base_run_metric = calculate_travel_time_and_return_weighted_sum_across_corridors(tm_run_id_base, year, tm_loaded_network_df_base, metrics_dict)
    # find the change in travel time for each corridor
    calculate_change_between_run_and_base(tm_run_id, tm_run_id_base, year, 'Reliable 1', metrics_dict)

    change_in_travel_time_weighted = this_run_metric[1] - base_run_metric[1]
    change_in_parallel_arterial_travel_time_weighted = this_run_metric[5] - base_run_metric[5]

    sum_of_weights = this_run_metric[0]
    sum_of_weights_parallel_arterial = this_run_metric[4]

    total_travel_time = this_run_metric[3] - base_run_metric[3]
    total_travel_time_parallel_arterial = this_run_metric[6] - base_run_metric[6]

    n = this_run_metric[2]

    # add average across corridors to metric dict
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Freeways','Fwy_avg_change_in_peak_hour_travel_time_across_key_corridors_weighted_by_vmt',year]      = change_in_travel_time_weighted/sum_of_weights
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Freeways','Fwy_simple_avg_change_in_peak_hour_travel_time_across_key_corridors',year]      = total_travel_time/n
    # parallel arterials
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Parallel Arterials','Parallel_Arterial_avg_change_in_peak_hour_travel_time_across_key_corridors_weighted_by_vmt',year]      = change_in_parallel_arterial_travel_time_weighted/sum_of_weights_parallel_arterial
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Parallel Arterials','Parallel_Arterial_simple_avg_change_in_peak_hour_travel_time_across_key_corridors',year]      = total_travel_time_parallel_arterial/n
    




def calculate_Reliable2_ratio_peak_nonpeak(tm_run_id, year, tm_loaded_network_df, metrics_dict):    
    # 6) Ratio of travel time during peak hours vs. non-peak hours between representative origin-destination pairs 

    metric_id = 'Reliable 2'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
    LOGGER.info("Calculating {} for {}".format(metric_id, tm_run_id))

    # columns: orig_taz, dest_taz, trip_mode, timeperiod_label, incQ, incQ_label, num_trips, avg_travel_time_in_mins
    ODTravelTime_byModeTimeperiod_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "core_summaries", ODTRAVELTIME_FILENAME)
    # TODO: this is large so join/subset it immediately
    tm_od_travel_times_df = pd.read_csv(ODTravelTime_byModeTimeperiod_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_od_travel_times_df), ODTravelTime_byModeTimeperiod_file))
    
    tm_od_travel_times_df = tm_od_travel_times_df.merge(taz_cities_df, left_on='orig_taz', right_on='taz1454', how='left', suffixes = ["",'_orig']).merge(taz_cities_df, left_on='dest_taz', right_on='taz1454', how='left', suffixes = ["",'_dest'])
    tm_od_travel_times_df = tm_od_travel_times_df.copy().loc[(tm_od_travel_times_df['trip_mode'] == 1)|(tm_od_travel_times_df['trip_mode'] == 2)|(tm_od_travel_times_df['trip_mode'] == 3)|(tm_od_travel_times_df['trip_mode'] == 4)|(tm_od_travel_times_df['trip_mode'] == 5)|(tm_od_travel_times_df['trip_mode'] == 6)|(tm_od_travel_times_df['trip_mode'] == 19)|(tm_od_travel_times_df['trip_mode'] == 20)|(tm_od_travel_times_df['trip_mode'] == 21)]
    tm_od_travel_times_df = tm_od_travel_times_df.loc[(tm_od_travel_times_df['avg_travel_time_in_mins'] > 0)]
    od_tt_peak_df = tm_od_travel_times_df.loc[(tm_od_travel_times_df['timeperiod_label'].str.contains("AM Peak") == True)|(tm_od_travel_times_df['timeperiod_label'].str.contains("PM Peak") == True)]
    od_tt_nonpeak_df = tm_od_travel_times_df.loc[(tm_od_travel_times_df['timeperiod_label'].str.contains("Midday") == True)]

     #  calcuate average across corridors
    n = 0 #counter to serve as denominator 
    total_travel_time = 0 #sum for numerator


    #     iterate through Origin Destination pairs, calculate metrics: average peak travel time, average nonpeak travel time, ratio of the two
    #     enter metrics into dictionary
    for od in NGFS_OD_CITIES_OF_INTEREST:
        peak_od_df = od_tt_peak_df.loc[(od_tt_peak_df['CITY'].str.contains(od[0]) == True)].loc[(od_tt_peak_df['CITY_dest'].str.contains(od[1]) == True), 'avg_travel_time_in_mins']
        num_peak_trips = od_tt_peak_df.loc[(od_tt_peak_df['CITY'].str.contains(od[0]) == True)].loc[(od_tt_peak_df['CITY_dest'].str.contains(od[1]) == True), 'num_trips']
        nonpeak_od_df = od_tt_nonpeak_df.loc[(od_tt_nonpeak_df['CITY'].str.contains(od[0]) == True)].loc[(od_tt_nonpeak_df['CITY_dest'].str.contains(od[1]) == True), 'avg_travel_time_in_mins']
        num_nonpeak_trips = od_tt_nonpeak_df.loc[(od_tt_nonpeak_df['CITY'].str.contains(od[0]) == True)].loc[(od_tt_nonpeak_df['CITY_dest'].str.contains(od[1]) == True), 'num_trips']
        
        try:
            avg_tt_peak_ORIG_DEST = numpy.average(a = peak_od_df, weights = num_peak_trips)
            avg_tt_nonpeak_ORIG_DEST = numpy.average(a = nonpeak_od_df, weights = num_nonpeak_trips)
        except:
            avg_tt_peak_ORIG_DEST = 0
            avg_tt_nonpeak_ORIG_DEST = 0
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra','{}_{}'.format(od[0],od[1]),'average peak travel time',year]      = avg_tt_peak_ORIG_DEST
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'extra','{}_{}'.format(od[0],od[1]),'average nonpeak travel time',year]      = avg_tt_nonpeak_ORIG_DEST
        if avg_tt_nonpeak_ORIG_DEST == 0:
            ratio_peak_nonpeak = 0
        else:
            ratio_peak_nonpeak = avg_tt_peak_ORIG_DEST/avg_tt_nonpeak_ORIG_DEST
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id,metric_id,'intermediate','{}_{}'.format(od[0],od[1]),'ratio of average peak to nonpeak travel time',year]      = ratio_peak_nonpeak
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'debug step','{}_{}'.format(od[0],od[1]),'observed number of peak trips',year]      = num_peak_trips.sum()
        metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'debug step','{}_{}'.format(od[0],od[1]),'observed number of nonpeak trips',year]      = num_nonpeak_trips.sum()

        # for od average calc
        n = n+1
        total_travel_time = total_travel_time + ratio_peak_nonpeak

    # add average across corridors to metric dict
    metrics_dict[grouping1, grouping2, grouping3, tm_run_id, metric_id,'final','Average across 10 O-D pairs','ratio_travel_time_peak_nonpeak_across_pairs',year]      = total_travel_time/n

    # add metric for goods routes: Calculate [Ratio of travel time during peak hours vs. non-peak hours] for 3 truck routes (using link-level)
    # load table with links for each goods route
    # columns: A, B, I580_I238_I880_PortOfOakland, I101_I880_PortOfOakland, I80_I880_PortOfOakland
    goods_routes_a_b_links_file = os.path.join(TM1_GIT_DIR, "utilities", "NextGenFwys", "metrics", "Input Files", "goods_routes_a_b.csv")
    # TODO: this is large so join/subset it immediately
    goods_routes_a_b_links_df = pd.read_csv(goods_routes_a_b_links_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(goods_routes_a_b_links_df), goods_routes_a_b_links_file))
    LOGGER.debug("goods_routes_a_b_links_df.head() =\n{}".format(goods_routes_a_b_links_df.head()))
    # merge loaded network with df containing route information
    # remove HOV lanes from the network
    loaded_network_with_goods_routes_df = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['USEAM'] == 1)]
    loaded_network_with_goods_routes_df = loaded_network_with_goods_routes_df.copy()[['a','b','ctimAM','ctimMD','ctimPM']]
    loaded_network_with_goods_routes_df = pd.merge(left=loaded_network_with_goods_routes_df, right=goods_routes_a_b_links_df, how='left', left_on=['a','b'], right_on=['A','B'])
    LOGGER.debug("loaded_network_with_goods_routes_df.head() =\n{}".format(loaded_network_with_goods_routes_df.head()))

    # sum the travel time for the different time periods on the route that begins on I580
    travel_time_route_I580_summed_df = loaded_network_with_goods_routes_df.copy().groupby('I580_I238_I880_PortOfOakland').agg('sum')
    LOGGER.debug("travel_time_route_I580_summed_df.head() =\n{}".format(travel_time_route_I580_summed_df.head()))
    # Only use rows containing 'AM' since this is the direction toward the port of oakland
    AM_travel_time_route_I580 = travel_time_route_I580_summed_df.loc['AM', 'ctimAM']
    MD_travel_time_route_I580 = travel_time_route_I580_summed_df.loc['AM', 'ctimMD']
    PM_travel_time_route_I580 = travel_time_route_I580_summed_df.loc['AM', 'ctimPM']
    # calculate average travel time for peak period
    peak_average_travel_time_route_I580 = numpy.mean([AM_travel_time_route_I580,PM_travel_time_route_I580])
    # calculate ratio for peak/offpeak
    ratio_peak_offpeak_route_I580 = peak_average_travel_time_route_I580 / MD_travel_time_route_I580
    # enter into metrics_dict
    metrics_dict['Goods Routes', 'Peak Hours', grouping3, tm_run_id, metric_id,'intermediate','I580_I238_I880_PortOfOakland', 'average peak travel time', year] = peak_average_travel_time_route_I580
    metrics_dict['Goods Routes', 'NonPeak Hours', grouping3, tm_run_id, metric_id,'intermediate','I580_I238_I880_PortOfOakland', 'average nonpeak travel time', year] = MD_travel_time_route_I580
    metrics_dict['Goods Routes', 'Peak vs NonPeak', grouping3, tm_run_id, metric_id,'final','I580_I238_I880_PortOfOakland', 'Ratio', year] = ratio_peak_offpeak_route_I580
    
    # sum the travel time for the different time periods on the route that begins on I101
    travel_time_route_I101_summed_df = loaded_network_with_goods_routes_df.copy().groupby('I101_I880_PortOfOakland').agg('sum')
    # Only use rows containing 'AM' since this is the direction toward the port of oakland
    AM_travel_time_route_I101 = travel_time_route_I101_summed_df.loc['AM', 'ctimAM']
    MD_travel_time_route_I101 = travel_time_route_I101_summed_df.loc['AM', 'ctimMD']
    PM_travel_time_route_I101 = travel_time_route_I101_summed_df.loc['AM', 'ctimPM']
    # calculate average travel time for peak period
    peak_average_travel_time_route_I101 = numpy.mean([AM_travel_time_route_I101,PM_travel_time_route_I101])
    # calculate ratio for peak/offpeak
    ratio_peak_offpeak_route_I101 = peak_average_travel_time_route_I101 / MD_travel_time_route_I101
    # enter into metrics_dict
    metrics_dict['Goods Routes', 'Peak Hours', grouping3, tm_run_id, metric_id,'intermediate','I101_I880_PortOfOakland', 'average peak travel time', year] = peak_average_travel_time_route_I101
    metrics_dict['Goods Routes', 'NonPeak Hours', grouping3, tm_run_id, metric_id,'intermediate','I101_I880_PortOfOakland', 'average nonpeak travel time', year] = MD_travel_time_route_I101
    metrics_dict['Goods Routes', 'Peak vs NonPeak', grouping3, tm_run_id, metric_id,'final','I101_I880_PortOfOakland', 'Ratio', year] = ratio_peak_offpeak_route_I101
    
    # sum the travel time for the different time periods on the route that begins on I80
    travel_time_route_I80_summed_df = loaded_network_with_goods_routes_df.copy().groupby('I80_I880_PortOfOakland').agg('sum')
    # Only use rows containing 'AM' since this is the direction toward the port of oakland
    AM_travel_time_route_I80 = travel_time_route_I80_summed_df.loc['AM', 'ctimAM']
    MD_travel_time_route_I80 = travel_time_route_I80_summed_df.loc['AM', 'ctimMD']
    PM_travel_time_route_I80 = travel_time_route_I80_summed_df.loc['AM', 'ctimPM']
    # calculate average travel time for peak period
    peak_average_travel_time_route_I80 = numpy.mean([AM_travel_time_route_I80,PM_travel_time_route_I80])
    # calculate ratio for peak/offpeak
    ratio_peak_offpeak_route_I80 = peak_average_travel_time_route_I80 / MD_travel_time_route_I80
    # enter into metrics_dict
    metrics_dict['Goods Routes', 'Peak Hours', grouping3, tm_run_id, metric_id,'intermediate','I80_I880_PortOfOakland', 'average peak travel time', year] = peak_average_travel_time_route_I80
    metrics_dict['Goods Routes', 'NonPeak Hours', grouping3, tm_run_id, metric_id,'intermediate','I80_I880_PortOfOakland', 'average nonpeak travel time', year] = MD_travel_time_route_I80
    metrics_dict['Goods Routes', 'Peak vs NonPeak', grouping3, tm_run_id, metric_id,'final','I80_I880_PortOfOakland', 'Ratio', year] = ratio_peak_offpeak_route_I80

def calculate_Reparative1_dollar_revenues_revinvested(tm_run_id, year, tm_scen_metrics_df, tm_auto_owned_df, tm_travel_cost_df, metrics_dict):
    # 7) Absolute dollar amount of new revenues generated that is reinvested in freeway adjacent communities

    # off model?
    metric_id = 'Reparative 1'




def calculate_Reparative2_ratio_revenues_revinvested(tm_run_id, year, tm_scen_metrics_df, tm_auto_owned_df, tm_travel_cost_df, metrics_dict):
    # 8) Ratio of new revenues paid for by low-income populations to revenues reinvested toward low-income populations

    # off model?
    metric_id = 'Reparative 2'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '




def adjust_fatalities_exp_speed(row, type_of_fatality):    
    # adjust fatalities based on exponents and speed. 
    # if fatalities/injuries are higher because speeds are higher in run than NP, use pmin function to replace with originally calculated FBP fatalities/injuries before VZ adjustment (do not let fatalities/injuries increase due to VZ adjustment calculation)
    N_type_fatalities = row[type_of_fatality]
    if N_type_fatalities==0 :
        return 0
    else:
        return numpy.minimum(N_type_fatalities*(row['Avg_speed']/row['Avg_Speed_No_Project'])**row['fatality_exponent'], N_type_fatalities)




def calculate_fatalitites(run_id, loaded_network_df, collision_rates_df, tm_loaded_network_df_no_project):
    NOTE_ON_FT_AND_AT = """
    FT is reclassified to -1 because the link is a dummy link (FT=6) and/or because lanes <=0. 
    FT is reclassified to -1 so that the join with fatality and injury rates doesn't match with anything
    and those links don't have any fatalities or injuries, so they are effectively filtered out.
    there is a travel model script to estimate fatalities in R
    https://github.com/BayAreaMetro/travel-model-one/blob/12962d73a5842b71b2439016e65d00b979af8f92/utilities/RTP/metrics/hwynet.py
    """
    LOGGER.info("{:,} rows from loaded_network_df".format(len(loaded_network_df)))
    LOGGER.debug("loaded_network_df.head() =\n{}".format(loaded_network_df.head()))
    modified_network_df = loaded_network_df.copy()
    modified_network_df['ft_collision'] = modified_network_df['ft']
    modified_network_df['at_collision'] = modified_network_df['at']
    modified_network_df.loc[modified_network_df['ft_collision'] == 1,'ft_collision'] = 2    # Freeway-to-freeway connector is like a freeway
    modified_network_df.loc[modified_network_df['ft_collision'] == 8,'ft_collision'] = 2    # managed freeway is like a freeway
    modified_network_df.loc[modified_network_df['ft_collision'] == 6,'ft_collision'] = -1   # ignore ft 6 (dummy links) and lanes <= 0 by replacing the ft with -1, which won't match with anything
    modified_network_df.loc[modified_network_df['lanes'] <= 0,'ft_collision'] = -1          # or those with lanes <= 0
    modified_network_df.loc[modified_network_df['ft_collision'] > 4,'ft_collision'] = 4     # cap at 4
    modified_network_df.loc[modified_network_df['at_collision'] < 3,'at_collision'] = 3     # this is different from hwynet.py but taken from PBA script
    modified_network_df.loc[modified_network_df['at_collision'] > 4,'at_collision'] = 4
    # ____confirm this is ok with FMS team and Anup______
    # filter for desired ft and remove links where all speeds are 0 <-- not sure if this is an error in the network
    modified_network_df = modified_network_df.loc[modified_network_df['ft_collision'] != -1]
    modified_network_df = modified_network_df.loc[modified_network_df['cspdAM'] != 0]

    modified_network_df = modified_network_df.merge(collision_rates_df.loc[collision_rates_df['year'] == 2035],how='left',left_on=['ft_collision','at_collision'],right_on=['ft','at'])
    LOGGER.info("{:,} rows from modified_network_df".format(len(modified_network_df)))
    LOGGER.debug("modified_network_df.head() =\n{}".format(modified_network_df.head()))

    # calculate population (only for relevant links/TAZs)
    relevant_links_ab = modified_network_df[['a','b']]
    LOGGER.info("{:,} rows from relevant_links_ab".format(len(relevant_links_ab)))
    LOGGER.debug("relevant_links_ab.head() =\n{}".format(relevant_links_ab.head()))
    # load network link to TAZ lookup file
    tm_network_links_taz_file = os.path.join(NGFS_SCENARIOS, run_id, "OUTPUT", "shapefile", "network_links_TAZ.csv")
    tm_network_links_taz_df = pd.read_csv(tm_network_links_taz_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_network_links_taz_df), tm_network_links_taz_file))
    relevant_taz_df = pd.merge(left=relevant_links_ab, right=tm_network_links_taz_df, how='left', left_on=['a','b'], right_on=['A','B']).drop_duplicates(subset='TAZ1454', keep='first')
    LOGGER.debug("relevant_taz_df.head() =\n{}".format(relevant_taz_df.head()))
    # load TAZ population data
    tm_taz_input_file     = os.path.join(NGFS_SCENARIOS, run_id, "INPUT", "landuse", "tazData.csv")
    tm_taz_input_df     = pd.read_csv(tm_taz_input_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_taz_input_df), tm_taz_input_file))

    # used to divide fatalities per 100K residents
    tm_taz_input_df = pd.merge(left=relevant_taz_df,
                                                        right=tm_taz_input_df,
                                                        left_on="TAZ1454",
                                                        right_on="ZONE",
                                                        how='left')
    # calculate population of area of interest
    LOGGER.debug("tm_taz_input_df.head() =\n{}".format(tm_taz_input_df.head()))
    Population = tm_taz_input_df.TOTPOP.sum()
    LOGGER.debug("Population =\n{}".format(Population))

    # calculate fatalities and injuries as they would be calculated without the speed reduction
    # "collisionLookup.csv" Units are collisions per 1,000,000 VMT
    # initialize columns to use in for loop
    modified_network_df['total_VMT'] = 0
    modified_network_df['sum_weighted_speeds'] = 0
    for timeperiod in ['EA','AM','MD','PM','EV']:
        # run for loop to compute vmt and weighted speeds (by vmt) for each time period, then sum together for weighted average
        # calculate vmt for each time period
        modified_network_df['vmt%s_tot' % timeperiod] = modified_network_df['vol%s_tot' % timeperiod]*modified_network_df['distance']
        # calculate weighted speed (vmt*speed) for each time period
        modified_network_df['weighted_speed_%s' % timeperiod] = modified_network_df['vmt%s_tot' % timeperiod]*modified_network_df['cspd%s' % timeperiod]
        # calculate numberator and denominator for weighted average
        # calculate total vmt for all timeperiods
        modified_network_df['total_VMT'] += modified_network_df['vmt%s_tot' % timeperiod]
        modified_network_df['sum_weighted_speeds'] += modified_network_df['weighted_speed_%s' % timeperiod]
    modified_network_df['annual_VMT'] = N_DAYS_PER_YEAR * modified_network_df['total_VMT']
    # compute a weighted average for speed (weights = vmt)
    modified_network_df['Avg_speed'] = modified_network_df['sum_weighted_speeds'] / modified_network_df['total_VMT']
    modified_network_df['N_motorist_fatalities'] = modified_network_df['Motor Vehicle Fatality'] * (modified_network_df['annual_VMT'] / 1000000) 
    modified_network_df['N_ped_fatalities'] = modified_network_df['Walk Fatality'] * (modified_network_df['annual_VMT'] / 1000000)
    modified_network_df['N_bike_fatalities'] = modified_network_df['Bike Fatality'] * (modified_network_df['annual_VMT'] / 1000000)
    modified_network_df['N_total_fatalities'] = modified_network_df['N_motorist_fatalities'] + modified_network_df['N_ped_fatalities'] + modified_network_df['N_bike_fatalities']
    modified_network_df['N_motorist_fatalities_per_100k_pop'] = modified_network_df['N_motorist_fatalities'] / (Population/100000) 
    modified_network_df['N_ped_fatalities_per_100k_pop'] = modified_network_df['N_ped_fatalities'] / (Population/100000)
    modified_network_df['N_bike_fatalities_per_100k_pop'] = modified_network_df['N_bike_fatalities'] / (Population/100000)
    modified_network_df['N_total_fatalities_per_100k_pop'] = modified_network_df['N_motorist_fatalities_per_100k_pop'] + modified_network_df['N_ped_fatalities_per_100k_pop'] + modified_network_df['N_bike_fatalities_per_100k_pop']
    LOGGER.debug("modified_network_df.head() =\n{}".format(modified_network_df.head()))

    if ('2015' not in run_id)&(NO_PROJECT_SCENARIO_RUN_ID != run_id): # need to make an adjustment here to exclude all no project runs, not just the most recent
        # join average speed on each link in no project
        # calculate average speed
        tm_loaded_network_df_no_project_copy = tm_loaded_network_df_no_project.copy()
        tm_loaded_network_df_no_project_copy['Avg_Speed_No_Project'] = (tm_loaded_network_df_no_project['cspdEA'] + tm_loaded_network_df_no_project['cspdAM'] + tm_loaded_network_df_no_project['cspdMD'] + tm_loaded_network_df_no_project['cspdPM'] + tm_loaded_network_df_no_project['cspdEV']) / 5
        no_project_network_avg_speed_df = tm_loaded_network_df_no_project_copy[['a','b', 'Avg_Speed_No_Project']]
        # merge DFs on 'a' & 'b'
        if 'Avg_Speed_No_Project' in modified_network_df.columns:
            modified_network_df.drop(columns=['Avg_Speed_No_Project'], inplace=True)
        modified_network_df = pd.merge(left=modified_network_df.copy(), right=no_project_network_avg_speed_df, how='left', left_on=['a','b'], right_on=['a','b'])
        # add attributes for fatality reduction exponent based on ft
        # exponents and methodology sourced from here: https://www.toi.no/getfile.php?mmfileid=13206 (table S1)
        # methodology cited in this FHWA resource: https://www.fhwa.dot.gov/publications/research/safety/17098/003.cfm
        # modified_network_df['fatality_exponent'] = 0
        modified_network_df.loc[(modified_network_df['ft_collision'] == 1) | (modified_network_df['ft_collision'] == 2) | (modified_network_df['ft_collision'] == 3) | (modified_network_df['ft_collision'] == 5) | (modified_network_df['ft_collision'] == 6) | (modified_network_df['ft_collision'] == 8),'fatality_exponent'] = 4.6
        modified_network_df.loc[(modified_network_df['ft_collision'] == 4) | (modified_network_df['ft_collision'] == 7),'fatality_exponent'] = 3
    
        modified_network_df['N_motorist_fatalities_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_motorist_fatalities'), axis = 1)
        modified_network_df['N_ped_fatalities_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_ped_fatalities'), axis = 1)
        modified_network_df['N_bike_fatalities_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_bike_fatalities'), axis = 1)
        modified_network_df['N_total_fatalities_speed_reduction'] = modified_network_df['N_motorist_fatalities_speed_reduction'] + modified_network_df['N_ped_fatalities_speed_reduction'] + modified_network_df['N_bike_fatalities_speed_reduction']

        modified_network_df['N_motorist_fatalities_per_100k_pop_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_motorist_fatalities_per_100k_pop'), axis = 1)
        modified_network_df['N_ped_fatalities_per_100k_pop_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_ped_fatalities_per_100k_pop'), axis = 1)
        modified_network_df['N_bike_fatalities_per_100k_pop_speed_reduction'] = modified_network_df.apply(lambda row: adjust_fatalities_exp_speed(row,'N_bike_fatalities_per_100k_pop'), axis = 1)
        modified_network_df['N_total_fatalities_per_100k_pop_speed_reduction'] = modified_network_df['N_motorist_fatalities_per_100k_pop_speed_reduction'] + modified_network_df['N_ped_fatalities_per_100k_pop_speed_reduction'] + modified_network_df['N_bike_fatalities_per_100k_pop_speed_reduction']
        modified_network_df['tm_run_id'] = tm_run_id
        LOGGER.debug("modified_network_df.head() =\n{}".format(modified_network_df.head()))
        # sum the metrics
        return modified_network_df.groupby('tm_run_id').agg({'N_motorist_fatalities_per_100k_pop': ['sum'],'N_motorist_fatalities_per_100k_pop_speed_reduction': ['sum'],
                                                             'N_bike_fatalities_per_100k_pop': ['sum'],'N_bike_fatalities_per_100k_pop_speed_reduction': ['sum'],
                                                             'N_ped_fatalities_per_100k_pop': ['sum'],'N_ped_fatalities_per_100k_pop_speed_reduction': ['sum'],
                                                             'N_total_fatalities_per_100k_pop': ['sum'],'N_total_fatalities_per_100k_pop_speed_reduction': ['sum'],
                                                             'N_motorist_fatalities': ['sum'],'N_motorist_fatalities_speed_reduction': ['sum'],
                                                             'N_bike_fatalities': ['sum'],'N_bike_fatalities_speed_reduction': ['sum'],
                                                             'N_ped_fatalities': ['sum'],'N_ped_fatalities_speed_reduction': ['sum'],
                                                             'N_total_fatalities': ['sum'],'N_total_fatalities_speed_reduction': ['sum']})
    else:
        modified_network_df['tm_run_id'] = tm_run_id
        LOGGER.debug("modified_network_df.head() =\n{}".format(modified_network_df.head()))
        return modified_network_df.groupby('tm_run_id').agg({'N_motorist_fatalities_per_100k_pop': ['sum'],'N_bike_fatalities_per_100k_pop': ['sum'],
                                                             'N_ped_fatalities_per_100k_pop': ['sum'],'N_total_fatalities_per_100k_pop': ['sum'],
                                                             'N_motorist_fatalities': ['sum'],'N_bike_fatalities': ['sum'],
                                                             'N_ped_fatalities': ['sum'],'N_total_fatalities': ['sum']})
 
def calculate_Safe1_fatalities_freeways_nonfreeways(tm_run_id, year, tm_loaded_network_df, metrics_dict):
    # 9) Annual number of estimated fatalities on freeways and non-freeway facilities

    # borrow from VZ_safety_calc_correction_v2.R
    # location \Box\Horizon and Plan Bay Area 2050\Equity and Performance\7_Analysis\Metrics\Metrics Development\Healthy\Fatalities Injuries
    
    NOTE_ON_CORRECTIONS = """
    the model does not do a great job with estimating fatalities. 
    the ratio of observed to calculated scales the modeled fatalities so that the modeled data matches observed data 
    (for example, if we know there were 400 fatalities in 2015 but the model says there were 200, the scaling factor would be x2. 
    that scaling factor should be kept in future years so that the magnitude is correct.
    the speed restrictions correction accounts for the fact that the likelihood of fatality or injury depends on the speed,
    but the model just calculates fatalities and injuries as a function of VMT and facility type (irrespective of speed). 
    we used research on the relationship between speed change and fatality rate to adjust fatalities and injuries down based on the PBA strategy to reduce speed limits on freeways and local streets.
    you should keep that adjustment if you change the speeds, consistent with PBA50.
    If you don't reduce speeds, don't keep the adjustment
    """
    metric_id = 'Safe 1'
    grouping1 = ' '
    grouping2 = ' '
    grouping3 = ' '
    LOGGER.info("Calculating {} for {}".format(metric_id, tm_run_id))

    fatality_df = calculate_fatalitites(tm_run_id, tm_loaded_network_df, collision_rates_df, tm_loaded_network_df_no_project)
    LOGGER.debug("fatality_df.head() =\n{}".format(fatality_df.head()))
    fatality_df_2015 = calculate_fatalitites(runid_2015, loaded_network_2015_df, collision_rates_df, tm_loaded_network_df_no_project)
    LOGGER.debug("fatality_df.head() =\n{}".format(fatality_df_2015.head()))

    # ______calculate fatalities for freeway and non-freeway________
    fwy_network_df = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['ft'] != 7)|(tm_loaded_network_df['ft'] != 4)|(tm_loaded_network_df['ft'] != 3)|(tm_loaded_network_df['ft'] != 6)]
    nonfwy_network_df = tm_loaded_network_df.copy().loc[(tm_loaded_network_df['ft'] == 7)|(tm_loaded_network_df['ft'] == 4)|(tm_loaded_network_df['ft'] == 3)]
    fwy_network_df_no_project = tm_loaded_network_df_no_project.copy().loc[(tm_loaded_network_df_no_project['ft'] != 7)|(tm_loaded_network_df_no_project['ft'] != 4)|(tm_loaded_network_df_no_project['ft'] != 3)|(tm_loaded_network_df_no_project['ft'] != 6)]
    nonfwy_network_df_no_project = tm_loaded_network_df_no_project.copy().loc[(tm_loaded_network_df_no_project['ft'] == 7)|(tm_loaded_network_df_no_project['ft'] == 4)|(tm_loaded_network_df_no_project['ft'] == 3)]
    fwy_fatality_df = calculate_fatalitites(tm_run_id, fwy_network_df, collision_rates_df, fwy_network_df_no_project)
    nonfwy_fatality_df = calculate_fatalitites(tm_run_id, nonfwy_network_df, collision_rates_df, nonfwy_network_df_no_project)
    
    # separate into variables for this run
    # output df has columns for each fatality type + 'after' which indicates that a correction was made for speed reductions in the scenario run

    # check for "No Project" in run id - all other projects implement vision zero strategies and thus have speed reductions (a fix is necessary to be able to run this function for 2015 runs)
    if NO_PROJECT_SCENARIO_RUN_ID == tm_run_id:
        N_motorist_fatalities = fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_ped_fatalities = fatality_df[('N_ped_fatalities_per_100k_pop','sum')][0]
        N_bike_fatalities = fatality_df[('N_bike_fatalities_per_100k_pop','sum')][0]
        N_motorist_fatalities_speed_reduction = N_motorist_fatalities
        N_ped_fatalities_speed_reduction = N_ped_fatalities
        N_bike_fatalities_speed_reduction = N_bike_fatalities
        # calculate and enter FWY AND NONFWY into metrics dict
        N_fwy_motorist_fatalities = fwy_fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_nonfwy_motorist_fatalities = nonfwy_fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_fwy_motorist_fatalities_speed_reduction = N_fwy_motorist_fatalities
        N_nonfwy_motorist_fatalities_speed_reduction = N_nonfwy_motorist_fatalities
    else:

        N_motorist_fatalities = fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_motorist_fatalities_speed_reduction = fatality_df[('N_motorist_fatalities_per_100k_pop_speed_reduction','sum')][0]
        N_ped_fatalities = fatality_df[('N_ped_fatalities_per_100k_pop','sum')][0]
        N_ped_fatalities_speed_reduction = fatality_df[('N_ped_fatalities_per_100k_pop_speed_reduction','sum')][0]
        N_bike_fatalities = fatality_df[('N_bike_fatalities_per_100k_pop','sum')][0]
        N_bike_fatalities_speed_reduction = fatality_df[('N_bike_fatalities_per_100k_pop_speed_reduction','sum')][0]
        N_total_fatalities = fatality_df[('N_total_fatalities_per_100k_pop','sum')][0]
        N_total_fatalities_speed_reduction = fatality_df[('N_total_fatalities_per_100k_pop_speed_reduction','sum')][0]

        # calculate and enter FWY AND NONFWY into metrics dict
        N_fwy_motorist_fatalities = fwy_fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_nonfwy_motorist_fatalities = nonfwy_fatality_df[('N_motorist_fatalities_per_100k_pop','sum')][0]
        N_fwy_motorist_fatalities_speed_reduction = fwy_fatality_df[('N_motorist_fatalities_per_100k_pop_speed_reduction','sum')][0]
        N_nonfwy_motorist_fatalities_speed_reduction = nonfwy_fatality_df[('N_motorist_fatalities_per_100k_pop_speed_reduction','sum')][0]

    # separate into variables for 2015 run
    N_motorist_fatalities_15 = fatality_df_2015[('N_motorist_fatalities','sum')][0]
    N_ped_fatalities_15 = fatality_df_2015[('N_ped_fatalities','sum')][0]
    N_bike_fatalities_15 = fatality_df_2015[('N_bike_fatalities','sum')][0]
    # N_total_fatalities_15 = fatality_df_2015[('N_total_fatalities','sum')][0]
    
    # calculate and enter into metrics dict
    N_motorist_fatalities_2015_obs_correction = N_motorist_fatalities*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    LOGGER.debug("2015 motorist correction factor =\n{}".format(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15))
    N_ped_fatalities_2015_obs_correction = N_ped_fatalities*(OBS_N_PED_FATALITIES_15/N_ped_fatalities_15)
    LOGGER.debug("2015 ped correction factor =\n{}".format(OBS_N_PED_FATALITIES_15/N_ped_fatalities_15))
    N_bike_fatalities_2015_obs_correction = N_bike_fatalities*(OBS_N_BIKE_FATALITIES_15/N_bike_fatalities_15)
    LOGGER.debug("2015 bike correction factor =\n{}".format(OBS_N_BIKE_FATALITIES_15/N_bike_fatalities_15))
    N_total_fatalities_2015_obs_correction = N_motorist_fatalities_2015_obs_correction + N_ped_fatalities_2015_obs_correction + N_bike_fatalities_2015_obs_correction
    
    N_motorist_fatalities_speed_reduction_2015_obs_correction = N_motorist_fatalities_speed_reduction*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    N_ped_fatalities_speed_reduction_2015_obs_correction = N_ped_fatalities_speed_reduction*(OBS_N_PED_FATALITIES_15/N_ped_fatalities_15)
    N_bike_fatalities_speed_reduction_2015_obs_correction = N_bike_fatalities_speed_reduction*(OBS_N_BIKE_FATALITIES_15/N_bike_fatalities_15)
    N_total_fatalities_speed_reduction_2015_obs_correction = N_motorist_fatalities_speed_reduction_2015_obs_correction + N_ped_fatalities_speed_reduction_2015_obs_correction + N_bike_fatalities_speed_reduction_2015_obs_correction
    
    metrics_dict['Motorist', 'No Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_motorist_fatalities_2015_obs_correction
    metrics_dict['Pedestrian', 'No Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_ped_fatalities_2015_obs_correction
    metrics_dict['Bicycle', 'No Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_bike_fatalities_2015_obs_correction
    metrics_dict['Total', 'No Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_total_fatalities_2015_obs_correction
    
    metrics_dict['Motorist', 'Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_motorist_fatalities_speed_reduction_2015_obs_correction
    metrics_dict['Pedestrian', 'Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_ped_fatalities_speed_reduction_2015_obs_correction
    metrics_dict['Bicycle', 'Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_bike_fatalities_speed_reduction_2015_obs_correction
    metrics_dict['Total', 'Vision Zero', grouping3, tm_run_id,metric_id,'intermediate','Mode','annual_number_of_fatalities',year] = N_total_fatalities_speed_reduction_2015_obs_correction

    N_fwy_motorist_fatalities_2015_obs_correction = N_fwy_motorist_fatalities*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    metrics_dict['Motorist', 'No Vision Zero', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_number_of_fwy_motorist_fatalities_2015_obs_correction',year] = N_fwy_motorist_fatalities_2015_obs_correction
    
    N_fwy_motorist_fatalities_speed_reduction_2015_obs_correction = N_fwy_motorist_fatalities_speed_reduction*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    metrics_dict['Motorist', 'Vision Zero', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_number_of_fwy_motorist_fatalities_speed_reduction_2015_obs_correction',year] = N_fwy_motorist_fatalities_speed_reduction_2015_obs_correction

    N_nonfwy_motorist_fatalities_2015_obs_correction = N_nonfwy_motorist_fatalities*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    metrics_dict['Motorist', 'No Vision Zero', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_number_of_nonfwy_motorist_fatalities_2015_obs_correction',year] = N_nonfwy_motorist_fatalities_2015_obs_correction

    N_nonfwy_motorist_fatalities_speed_reduction_2015_obs_correction = N_nonfwy_motorist_fatalities_speed_reduction*(OBS_N_MOTORIST_FATALITIES_15/N_motorist_fatalities_15)
    metrics_dict['Motorist', 'Vision Zero', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_number_of_nonfwy_motorist_fatalities_speed_reduction_2015_obs_correction',year] = N_nonfwy_motorist_fatalities_speed_reduction_2015_obs_correction

    # Calculate equity metric: rate of non-freeway fatalities (per 100K residents) in region vs. EPCs
    # calculated using vmt_vht_metrics_by_taz.csv, but can also compute at the link level using network_links_TAZ.csv
    # load fatalities metrics df
    vmt_vht_metrics_by_taz_file    = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "metrics", "vmt_vht_metrics_by_taz.csv")
    vmt_vht_metrics_by_taz_df      = pd.read_csv(vmt_vht_metrics_by_taz_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(vmt_vht_metrics_by_taz_df), vmt_vht_metrics_by_taz_file))

    # join to epc lookup table
    vmt_vht_metrics_by_taz_df = pd.merge(left=vmt_vht_metrics_by_taz_df,
                                                        right=NGFS_EPC_TAZ_DF,
                                                        left_on="TAZ1454",
                                                        right_on="TAZ1454",
                                                        how='left')
    # make a copy and filter for EPCs
    vmt_vht_metrics_by_epc_taz_df = vmt_vht_metrics_by_taz_df.copy().loc[vmt_vht_metrics_by_taz_df['taz_epc'] == 1]
    # load taz data to pull population from
    tm_taz_input_file     = os.path.join(NGFS_SCENARIOS, tm_run_id, "INPUT", "landuse", "tazData.csv")
    tm_taz_input_df     = pd.read_csv(tm_taz_input_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_taz_input_df), tm_taz_input_file))

    # sum the fatalities by mode and divide per 100K residents
    tm_taz_input_df = pd.merge(left=tm_taz_input_df,
                                                        right=NGFS_EPC_TAZ_DF,
                                                        left_on="ZONE",
                                                        right_on="TAZ1454",
                                                        how='left')
    # calculate population of region
    region_population = tm_taz_input_df.TOTPOP.sum()
    # calculate population of EPC TAZs
    epc_population = tm_taz_input_df.copy().loc[tm_taz_input_df['taz_epc'] == 1]
    epc_population = epc_population.TOTPOP.sum()
    # filter for fwy vs nonfreeway, sum fatalities across relevant TAZs, divide per 100K residents
    # fwy:
    #   for entire region:
    annual_fwy_motorist_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'freeway', 'Motor Vehicle Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_walk_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'freeway', 'Walk Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_bike_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'freeway', 'Bike Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_total_fatalities_region = annual_fwy_motorist_fatalities_region + annual_fwy_walk_fatalities_region + annual_fwy_bike_fatalities_region
    #   for EPCs:
    annual_fwy_motorist_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'freeway', 'Motor Vehicle Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_walk_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'freeway', 'Walk Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_bike_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'freeway', 'Bike Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_fwy_total_fatalities_epc = annual_fwy_motorist_fatalities_epc + annual_fwy_walk_fatalities_epc + annual_fwy_bike_fatalities_epc
    #   for rest of the region
    annual_fwy_motorist_fatalities_nonepc = annual_fwy_motorist_fatalities_region - annual_fwy_motorist_fatalities_epc
    annual_fwy_walk_fatalities_nonepc = annual_fwy_walk_fatalities_region - annual_fwy_walk_fatalities_epc
    annual_fwy_bike_fatalities_nonepc = annual_fwy_bike_fatalities_region - annual_fwy_bike_fatalities_epc
    annual_fwy_total_fatalities_nonepc = annual_fwy_total_fatalities_region - annual_fwy_total_fatalities_epc

    # nonfwy:
    #   for entire region:
    annual_nonfwy_motorist_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'non-freeway', 'Motor Vehicle Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_walk_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'non-freeway', 'Walk Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_bike_fatalities_region = vmt_vht_metrics_by_taz_df.loc[vmt_vht_metrics_by_taz_df['road_type'] == 'non-freeway', 'Bike Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_total_fatalities_region = annual_nonfwy_motorist_fatalities_region + annual_nonfwy_walk_fatalities_region + annual_nonfwy_bike_fatalities_region
    #   for EPCs:
    annual_nonfwy_motorist_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'non-freeway', 'Motor Vehicle Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_walk_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'non-freeway', 'Walk Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_bike_fatalities_epc = vmt_vht_metrics_by_epc_taz_df.loc[vmt_vht_metrics_by_epc_taz_df['road_type'] == 'non-freeway', 'Bike Fatality'].sum() * N_DAYS_PER_YEAR / region_population * PER_X_PEOPLE
    annual_nonfwy_total_fatalities_epc = annual_nonfwy_motorist_fatalities_epc + annual_nonfwy_walk_fatalities_epc + annual_nonfwy_bike_fatalities_epc
    #   for rest of the region
    annual_nonfwy_motorist_fatalities_nonepc = annual_nonfwy_motorist_fatalities_region - annual_nonfwy_motorist_fatalities_epc
    annual_nonfwy_walk_fatalities_nonepc = annual_nonfwy_walk_fatalities_region - annual_nonfwy_walk_fatalities_epc
    annual_nonfwy_bike_fatalities_nonepc = annual_nonfwy_bike_fatalities_region - annual_nonfwy_bike_fatalities_epc
    annual_nonfwy_total_fatalities_nonepc = annual_nonfwy_total_fatalities_region - annual_nonfwy_total_fatalities_epc

    # enter into metrics dict
    metrics_dict['Motorist', 'Region', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_motorist_fatalities_region
    metrics_dict['Walk', 'Region', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_walk_fatalities_region
    metrics_dict['Bike', 'Region', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_bike_fatalities_region
    metrics_dict['Total', 'Region', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_total_fatalities_region
    metrics_dict['Motorist', 'EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_motorist_fatalities_epc
    metrics_dict['Walk', 'EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_walk_fatalities_epc
    metrics_dict['Bike', 'EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_bike_fatalities_epc
    metrics_dict['Total', 'EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_total_fatalities_epc
    metrics_dict['Motorist', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_motorist_fatalities_nonepc
    metrics_dict['Walk', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_walk_fatalities_nonepc
    metrics_dict['Bike', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_bike_fatalities_nonepc
    metrics_dict['Total', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_fwy_total_fatalities_nonepc

    metrics_dict['Motorist', 'Region', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_motorist_fatalities_region
    metrics_dict['Walk', 'Region', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_walk_fatalities_region
    metrics_dict['Bike', 'Region', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_bike_fatalities_region
    metrics_dict['Total', 'Region', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_total_fatalities_region
    metrics_dict['Motorist', 'EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_motorist_fatalities_epc
    metrics_dict['Walk', 'EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_walk_fatalities_epc
    metrics_dict['Bike', 'EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_bike_fatalities_epc
    metrics_dict['Total', 'EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_total_fatalities_epc
    metrics_dict['Motorist', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_motorist_fatalities_nonepc
    metrics_dict['Walk', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_walk_fatalities_nonepc
    metrics_dict['Bike', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_bike_fatalities_nonepc
    metrics_dict['Total', 'Non-EPCs', grouping3, tm_run_id,metric_id,'final','Non-Freeway Facilities','annual_fatalities (per 100K residents)',year] = annual_nonfwy_total_fatalities_nonepc

def calculate_Safe2_change_in_vmt(tm_run_id: str) -> pd.DataFrame:
    """ Calculates Safety 2: Change in vehicle miles travelled (VMT) on freeway and non-freeway facilities
    Additionally, calculates VMT segmented by different categories (households by income, non-houehold and trucks)
    and VMT segmented by whether or not the links are located in Equity Priority Communities (EPC) TAZS.

    Args:
        tm_run_id (str): Travel model run ID
    
    Returns:
        pd.DataFrame: with columns a subset of METRICS_COLUMNS, including
          metric_id          = 'Safe1'
          modelrun_id        = tm_run_id
          intermediate/final = final
        Metrics return:
          grouping1              key                                    metric_desc
          Income Level           inc[1234]                              VMT|VHT  (category breakdown)
          Non-Household          air|ix|zpv_tnc                         VMT|VHT
          Truck                  truck                                  VMT|VHT
          Freeway|Non-Freeway    Freeway|Arterial|Collector|Expressway  VMT|VHT  (facility type breakdown)
          Freeway|Non-Freeway    EPCs|Non-EPCs|Region                   VMT|VHT  (EPC/non-EPC breakdown)

    Notes: Uses
    * auto_times.csv (for category breakdown)
    * avgload5period.csv (for facility type breakdown)
    * vmt_vht_metrics_by_taz.csv (for EPC/non-EPC breakdown)
    """
    METRIC_ID = 'Safe 2'
    metric_id = METRIC_ID
    LOGGER.info("Calculating {} for {}".format(METRIC_ID, tm_run_id))

    # read network-based auto times
    auto_times_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "metrics", "auto_times.csv")
    auto_times_df = pd.read_csv(auto_times_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(auto_times_df), auto_times_file))

    # we'll summarize by these
    auto_times_df['grouping1'] = 'Income Level'
    auto_times_df['key']      = auto_times_df['Income']  # for households, use income
    auto_times_df.loc[ auto_times_df.Mode.str.endswith('ix'),  ['grouping1', 'key']] = ['Non-Household', 'ix'     ]
    auto_times_df.loc[ auto_times_df.Mode.str.endswith('air'), ['grouping1', 'key']] = ['Non-Household', 'air'    ]
    auto_times_df.loc[ auto_times_df.Mode == 'zpv_tnc',        ['grouping1', 'key']] = ['Non-Household', 'zpv_tnc']
    auto_times_df.loc[ auto_times_df.Mode == 'truck',          ['grouping1', 'key']] = ['Truck',         'truck'  ]

    auto_times_df = auto_times_df.groupby(by=['grouping1','key']).agg({'Vehicle Miles':'sum', 'Vehicle Minutes':'sum'}).reset_index()
    auto_times_df['VHT'] = auto_times_df['Vehicle Minutes']/60.0
    auto_times_df.drop(columns=['Vehicle Minutes'], inplace=True)
    auto_times_df.rename(columns={'Vehicle Miles':'VMT'}, inplace=True)
    LOGGER.debug("auto_times_df:\n{}".format(auto_times_df))

    loaded_network_file = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "avgload5period.csv")
    loaded_network_df = pd.read_csv(loaded_network_file)
    loaded_network_df.rename(columns=lambda x: x.strip(), inplace=True)
    LOGGER.info("  Read {:,} rows from {}".format(len(loaded_network_df), loaded_network_file))
    LOGGER.debug("  Columns:".format(list(loaded_network_df.columns)))

    # compute Fwy and Non_Fwy VMT
    loaded_network_df['VMT'] = \
        (loaded_network_df['volEA_tot']+
         loaded_network_df['volAM_tot']+
         loaded_network_df['volMD_tot']+
         loaded_network_df['volPM_tot']+
         loaded_network_df['volEV_tot'])*loaded_network_df['distance']
    loaded_network_df['VHT'] = (\
        (loaded_network_df['ctimEA']*loaded_network_df['volEA_tot']) + \
        (loaded_network_df['ctimAM']*loaded_network_df['volAM_tot']) + \
        (loaded_network_df['ctimMD']*loaded_network_df['volMD_tot']) + \
        (loaded_network_df['ctimPM']*loaded_network_df['volPM_tot']) + \
        (loaded_network_df['ctimEV']*loaded_network_df['volEV_tot']))/60.0
    
    # https://github.com/BayAreaMetro/modeling-website/wiki/MasterNetworkLookupTables#facility-type-ft
    ft_to_grouping_key_df = pd.DataFrame(columns=['ft','grouping1','key'], data=[
        ( 1, 'Freeway',    'Freeway'   ), # freeway-to-freeway connector
        ( 2, 'Freeway',    'Freeway'   ), # freeway
        ( 3, 'Non-Freeway','Expressway'), # expressway
        ( 4, 'Non-Freeway','Collector' ), # collector
        ( 5, None,          None       ), # freeway ramp
        ( 6, None,          None       ), # dummy link
        ( 7, 'Non-Freeway','Arterial'  ), # major arterial
        ( 8, 'Freeway',    'Freeway'   ), # managed freeway
        ( 9, None,          None       ), # special facility
        (10, None,          None       )  # toll plaza
    ])
    # NOTE: this is inconsistent with the vmt_vht_metrics.csv road_type for 'non-freeway' which includes
    # ft [1,2,3,8] as 'freeway' and all others as 'non-freeway'
    # https://github.com/BayAreaMetro/travel-model-one/blob/78fb93e881348f794e3423f3a987753a0eef1255/utilities/RTP/metrics/hwynet.py#L334

    LOGGER.debug("  Using facility type categories:\n{}".format(ft_to_grouping_key_df))
    loaded_network_df = pd.merge(left=loaded_network_df, right=ft_to_grouping_key_df, on='ft', how='left')
    ft_metrics_df = loaded_network_df.groupby(by=['grouping1','key']).agg({'VMT':'sum', 'VHT':'sum'}).reset_index()
    LOGGER.debug("ft_metrics_df:\n{}".format(ft_metrics_df))

    # Calculate equity metric: non-freeway VMT in region and EPCs
    # calculated using vmt_vht_metrics_by_taz.csv, but can also compute at the link level using network_links_TAZ.csv
    # load vmt metrics df
    vmt_vht_metrics_by_taz_file    = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "metrics", "vmt_vht_metrics_by_taz.csv")
    vmt_vht_metrics_by_taz_df      = pd.read_csv(vmt_vht_metrics_by_taz_file)
    LOGGER.info("  Read {:,} rows from {}".format(len(vmt_vht_metrics_by_taz_df), vmt_vht_metrics_by_taz_file))

    # join to epc lookup table
    vmt_vht_metrics_by_taz_df = pd.merge(
        left=vmt_vht_metrics_by_taz_df,
        right=NGFS_EPC_TAZ_DF,
        left_on="TAZ1454",
        right_on="TAZ1454",
        how='left')
    # LOGGER.debug("vmt_vht_metrics_by_taz_df.head():\n{}".format(vmt_vht_metrics_by_taz_df))

    # capitalize to be consistent with above
    vmt_vht_metrics_by_taz_df.loc[ vmt_vht_metrics_by_taz_df.road_type=='freeway',     'road_type' ] = 'Freeway'
    vmt_vht_metrics_by_taz_df.loc[ vmt_vht_metrics_by_taz_df.road_type=='non-freeway', 'road_type' ] = 'Non-Freeway'
    # Recode
    vmt_vht_metrics_by_taz_df['key'] = 'Non-EPCs'
    vmt_vht_metrics_by_taz_df.loc[ vmt_vht_metrics_by_taz_df.taz_epc == 1, 'key'] = 'EPCs'
    # Summarize
    epc_metrics_df    = vmt_vht_metrics_by_taz_df.groupby(by=['road_type','key']).agg({'VMT':'sum','VHT':'sum'}).reset_index()
    region_metrics_df = vmt_vht_metrics_by_taz_df.groupby(by=['road_type'      ]).agg({'VMT':'sum','VHT':'sum'}).reset_index()
    region_metrics_df['key'] = 'Region'
    # Combine
    epc_metrics_df = pd.concat([epc_metrics_df, region_metrics_df])
    epc_metrics_df.rename(columns={'road_type':'grouping1'}, inplace=True)
    LOGGER.debug("epc_metrics_df\n{}".format(epc_metrics_df))

    # put it together, move to long form and return
    metrics_df = pd.concat([auto_times_df, ft_metrics_df, epc_metrics_df])
    metrics_df = metrics_df.melt(id_vars=['grouping1','key'], var_name='metric_desc')
    metrics_df['modelrun_id'] = tm_run_id
    metrics_df['metric_id'] = METRIC_ID
    metrics_df['intermediate/final'] = 'final'
    metrics_df['year'] = tm_run_id[:4]
    LOGGER.debug("metrics_df for Safe 2:\n{}".format(metrics_df))

    return metrics_df

def metrics_dict_to_df(metrics_dict: dict) -> pd.DataFrame:
    """
    Temporary method to convert metrics_dict to metrics_df (pd.DataFrame) since the dictionary structure just makes this more confusing

    Returns DataFrame with columns: grouping1, grouping2, grouping3, modelrun_id, metric_id, metric_level, key, metric_desc, year, value
    """
    # key=grouping1, grouping2, grouping3, tm_run_id, metric_id, top_level|extra|intermediate|final, key, metric_desc, year
    row_dict_list = []
    for metric_key in metrics_dict.keys():
        # keys given by METRICS_COLUMNS
        row_dict = {METRICS_COLUMNS[idx]: metric_key[idx] for idx in range(len(metric_key))}
        # metric value
        row_dict['value'] = metrics_dict[metric_key]
        row_dict_list.append(row_dict)
    return pd.DataFrame.from_records(row_dict_list)

def determine_tolled_minor_group_links(tm_run_id: str, fwy_or_arterial: str) -> pd.DataFrame:
    """ Given a travel model run ID, reads the loaded network and the tollclass designations,
    and returns a table that will be used to define which links belong to which tollclass minor grouping.

    If fwy_or_arterial == "fwy",      tm_run_id should be a Pathway 1 model run, and this will return tolled freeway links
    If fwy_or_arterial == "arterial", tm_run_id should be a Pathway 2 model run, and this will return arterial freeway links

    This replaces 'Input Files\\a_b_with_minor_groupings.csv' because this uses the model network information directly

    Args:
        tm_run_id (str):      travel model run ID (should be Pathway 1 or 2)
        fwy_or_arterial(str): one of "fwy" or "arterial"

    Returns:
        pd.DataFrame: mapping from links to tollclass minor groupings.  Columns:
        a (int):              link A node
        b (int):              link B node
        grouping (str):       minor grouping without direction, e.g. EastBay_68024980, EastBay_880680, etc.
        grouping_dir (str):   either AM or PM for the grouping
    """
    if fwy_or_arterial not in ["fwy","arterial"]: raise ValueError

    LOGGER.info("=== determine_tolled_minor_group_links({}, {}) ===".format(tm_run_id, fwy_or_arterial))
    loaded_roadway_network = os.path.join(NGFS_SCENARIOS, tm_run_id, "OUTPUT", "avgload5period_vehclasses.csv")
    tm_loaded_network_df = pd.read_csv(loaded_roadway_network, 
                                       usecols=['a','b','tollclass','ft'],
                                       dtype={'a':numpy.int64, 'b':numpy.int64, 'tollclass':numpy.int64},
                                       na_values=[''])
    LOGGER.info("  Read {:,} rows from {}".format(len(tm_loaded_network_df), loaded_roadway_network))

    # read toll class groupings
    tollclass_df = pd.read_excel(NGFS_TOLLCLASS_FILE)
    LOGGER.info("  Read {:,} rows from {}".format(len(tollclass_df), NGFS_TOLLCLASS_FILE))
    # select NextGenFwy tollclasses where 'Grouping minor' exists
    tollclass_df = tollclass_df.loc[(tollclass_df.project == 'NextGenFwy') & pd.notna(tollclass_df['Grouping minor'])]

    # See TOLLCLASS_Designations.xlsx workbook, Readme - numbering convention
    if fwy_or_arterial == "fwy":
        tollclass_df = tollclass_df.loc[tollclass_df.tollclass > 900000]
    elif fwy_or_arterial == "arterial":
        tollclass_df = tollclass_df.loc[(tollclass_df.tollclass > 700000) & 
                                        (tollclass_df.tollclass < 900000)]

    LOGGER.info("  Filtered to {:,} rows for project=='NextGenFwy' with notna 'Grouping minor' and tollclass appropriate to {}".format(
        len(tollclass_df), fwy_or_arterial))
    # LOGGER.info("  Grouping minor: {}".format(sorted(tollclass_df['Grouping minor'].to_list())))

    # add to loaded roadway network -- INNER JOIN
    grouping_df = pd.merge(
        left=tm_loaded_network_df,
        right=tollclass_df[['tollclass','Grouping minor']],
        on=['tollclass'],
        how='inner'
    )
    # remove rows with 'Minor grouping' that doesn't end in AM or PM
    grouping_df = grouping_df.loc[
        grouping_df['Grouping minor'].str.endswith('_AM') |
        grouping_df['Grouping minor'].str.endswith('_PM')
    ]

    # log the facility type summary
    LOGGER.debug("  Tolled {} facility types:\n{}".format(fwy_or_arterial, grouping_df['ft'].value_counts()))

    # split 'Grouping minor' to 'grouping' (now without direction) and 'grouping_dir'
    grouping_df['grouping_dir'] = grouping_df['Grouping minor'].str[-2:]
    grouping_df['grouping']     = grouping_df['Grouping minor'].str[:-3]
    grouping_df.drop(columns=['Grouping minor','tollclass','ft'], inplace=True)
    LOGGER.debug("  Returning {:,} links:\n{}".format(len(grouping_df), grouping_df))
    return grouping_df

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip_if_exists", action="store_true", help="Use this option to skip creating metrics files if one exists already")
    args = parser.parse_args()

    pd.options.display.width = 500 # redirect output to file so this will be readable
    pd.options.display.max_columns = 100
    pd.options.display.max_rows = 500
    pd.options.mode.chained_assignment = None  # default='warn'

    # set up logging
    # create logger
    LOGGER = logging.getLogger(__name__)
    LOGGER.setLevel('DEBUG')

    # console handler
    ch = logging.StreamHandler()
    ch.setLevel('INFO')
    ch.setFormatter(logging.Formatter('%(message)s', datefmt='%m/%d/%Y %I:%M:%S %p'))
    LOGGER.addHandler(ch)
    # file handler -- append if skip_if_exists is passed
    fh = logging.FileHandler(LOG_FILE, mode='a' if args.skip_if_exists else 'w')
    fh.setLevel('DEBUG')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p'))
    LOGGER.addHandler(fh)

    LOGGER.debug("args = {}".format(args))

    current_runs_df = pd.read_excel(NGFS_MODEL_RUNS_FILE, sheet_name='all_runs', usecols=['project','year','directory','run_set','category','short_name','status'])
    current_runs_df = current_runs_df.loc[ current_runs_df['status'] == 'current']
    # only process metrics for 2035 model runs 
    current_runs_df = current_runs_df.loc[ current_runs_df['year'] == 2035]
    # # TODO: delete later after NP10 runs are completed
    # current_runs_df = current_runs_df.loc[ (current_runs_df['directory'].str.contains('NP10') == False)]

    LOGGER.info("current_runs_df: \n{}".format(current_runs_df))

    current_runs_list = current_runs_df['directory'].to_list()
    
    # load lookup file for a city's TAZs
    taz_cities_df = pd.read_csv('L:\\Application\\Model_One\\NextGenFwys\\metrics\\Input Files\\taz_with_cities.csv')

    # load minor groupings, to be merged with loaded network
    minor_links_df = pd.read_csv('L:\\Application\\Model_One\\NextGenFwys\\metrics\\Input Files\\a_b_with_minor_groupings.csv')

    # list for iteration
    minor_groups = minor_links_df['Grouping minor'].unique()[1:] #exclude 'other' and NaN
    minor_groups = numpy.delete(minor_groups, 2)

    # load lookup file for parallel arterial links
    parallel_arterials_links = pd.read_csv('L:\\Application\\Model_One\\NextGenFwys\\metrics\\Input Files\\ParallelArterialLinks.csv')
    # TODO: remove all instances of merging on an extra 'a_b' column
    parallel_arterials_links['a_b'] = parallel_arterials_links['A'].astype(str) + "_" + parallel_arterials_links['B'].astype(str)

    # define base run inputs
    # # base year run for comparisons = most recent Pathway 4 (No New Pricing) run
    pathway4_runs = current_runs_df.loc[ current_runs_df['category']=="Pathway 4" ]
    BASE_SCENARIO_RUN_ID = pathway4_runs['directory'].tolist()[-1] # take the last one

    noproject_runs = current_runs_df.loc[ current_runs_df['category'] == 'No Project']
    NO_PROJECT_SCENARIO_RUN_ID = noproject_runs['directory'].tolist()[-1] # take the last one
    tm_run_id_base = BASE_SCENARIO_RUN_ID # todo: deprecate this
    LOGGER.info("=> BASE_SCENARIO_RUN_ID = {}".format(BASE_SCENARIO_RUN_ID))

    # find the last pathway 1 run, since we'll use that to determine which links are in the fwy minor groupings
    pathway1_runs = current_runs_df.loc[ current_runs_df['category'].str.startswith("Pathway 1")]
    PATHWAY1_SCENARIO_RUN_ID = pathway1_runs['directory'].tolist()[-1] # take the last one
    LOGGER.info("=> PATHWAY1_SCENARIO_RUN_ID = {}".format(PATHWAY1_SCENARIO_RUN_ID))
    TOLLED_FWY_MINOR_GROUP_LINKS_DF = determine_tolled_minor_group_links(PATHWAY1_SCENARIO_RUN_ID, "fwy")

    # find the last pathway 2 run, since we'll use that to determine which links are in the tolled arterial minor groupings
    pathway2_runs = current_runs_df.loc[ current_runs_df['category'].str.startswith("Pathway 2")]
    PATHWAY2_SCENARIO_RUN_ID = pathway2_runs['directory'].tolist()[-1] # take the last one
    LOGGER.info("=> PATHWAY2_SCENARIO_RUN_ID = {}".format(PATHWAY2_SCENARIO_RUN_ID))
    TOLLED_ART_MINOR_GROUP_LINKS_DF = determine_tolled_minor_group_links(PATHWAY2_SCENARIO_RUN_ID, "arterial")

    # ______load base scenario network to use for speed comparisons in vmt corrections______
    tm_run_location_base = os.path.join(NGFS_SCENARIOS, BASE_SCENARIO_RUN_ID)
    if ODTRAVELTIME_FILENAME == "ODTravelTime_byModeTimeperiod_reduced_file.csv":
        network_links_dbf_base = pd.read_csv(tm_run_location_base + '\\OUTPUT\\shapefile\\network_links_reduced_file.csv')
    else:
        # addding back original code for simplicity of steps to update the tableau workbook (at the cost of run time)
        input_file = tm_run_location_base + '\\OUTPUT\\shapefile\\network_links.DBF'
        LOGGER.info("Reading {}".format(input_file))
        dbf = simpledbf.Dbf5(input_file)
        network_links_dbf_base = dbf.to_dataframe()
        network_links_dbf_base['a_b'] = network_links_dbf_base['A'].astype(str) + "_" + network_links_dbf_base['B'].astype(str)
    # ______define the base run inputs for "change in" comparisons______
    tm_scen_metrics_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/metrics/scenario_metrics.csv',names=["runid", "metric_name", "value"])
    tm_auto_owned_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/metrics/autos_owned.csv')
    tm_travel_cost_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/core_summaries/TravelCost.csv')
    tm_auto_times_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/metrics/auto_times.csv',sep=",")#, index_col=[0,1])
    tm_loaded_network_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/avgload5period.csv')
    tm_loaded_network_df_base = tm_loaded_network_df_base.rename(columns=lambda x: x.strip())
    # merging df that has the list of minor segments with loaded network - for corridor analysis
    tm_loaded_network_df_base['a_b'] = tm_loaded_network_df_base['a'].astype(str) + "_" + tm_loaded_network_df_base['b'].astype(str)
    tm_loaded_network_df_base = tm_loaded_network_df_base.copy().merge(network_links_dbf_base.copy(), on='a_b', how='left')
    tm_loaded_network_df_base = tm_loaded_network_df_base.merge(minor_links_df, on='a_b', how='left')

    # ______load no project network to use for speed comparisons in vmt corrections______
    tm_run_location_no_project = os.path.join(NGFS_SCENARIOS, NO_PROJECT_SCENARIO_RUN_ID)
    tm_loaded_network_df_no_project = pd.read_csv(tm_run_location_no_project+'/OUTPUT/avgload5period.csv')
    tm_loaded_network_df_no_project = tm_loaded_network_df_no_project.rename(columns=lambda x: x.strip())
    # merging df that has the list of minor segments with loaded network - for corridor analysis
    tm_loaded_network_df_no_project['a_b'] = tm_loaded_network_df_no_project['a'].astype(str) + "_" + tm_loaded_network_df_no_project['b'].astype(str)
    tm_loaded_network_df_no_project = tm_loaded_network_df_no_project.copy().merge(network_links_dbf_base.copy(), on='a_b', how='left')
    tm_loaded_network_df_no_project = tm_loaded_network_df_no_project.merge(minor_links_df, on='a_b', how='left')

    # load vmt_vht_metrics.csv for vmt calc
    tm_vmt_metrics_df_base = pd.read_csv(tm_run_location_base + '/OUTPUT/metrics/vmt_vht_metrics.csv', sep=",", index_col=[0,1])
    # load transit_times_by_mode_income.csv
    tm_transit_times_df_base = pd.read_csv(tm_run_location_base + '/OUTPUT/metrics/transit_times_by_mode_income.csv', sep=",", index_col=[0,1])
    # load VehicleMilesTraveled_households.csv
    vmt_hh_df_base = pd.read_csv(tm_run_location_base+'/OUTPUT/core_summaries/VehicleMilesTraveled_households.csv')

    for tm_run_id in current_runs_list:
        out_filename = os.path.join(os.getcwd(),"ngfs_metrics_{}.csv".format(tm_run_id))

        if args.skip_if_exists and os.path.exists(out_filename):
            LOGGER.info("Skipping {} -- {} exists".format(tm_run_id, out_filename))
            continue

        LOGGER.info("Processing run {}".format(tm_run_id))

        # #temporary run location for testing purposes
        tm_run_location = os.path.join(NGFS_SCENARIOS, tm_run_id)

        # metric dict input: year
        year = tm_run_id[:4]
        # manually calculated sums for discounts, credits, and rebates
        # adjust later
        # TODO: What are these?
        if ('1b' in tm_run_id) | ('2b' in tm_run_id) | ('3b' in tm_run_id): #how to include discounts for persons with disabilities?
          Q1_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0.5
          Q2_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0
          Q3_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0
          Q4_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0

        else:
          Q1_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0
          Q2_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0
          Q3_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0
          Q4_TOLL_DISCOUNTS_HIGHWAYS_ARTERIALS = 1 - 0

        # ______define the inputs_______
        tm_scen_metrics_df = pd.read_csv(tm_run_location+'/OUTPUT/metrics/scenario_metrics.csv',names=["runid", "metric_name", "value"])
        tm_auto_owned_df = pd.read_csv(tm_run_location+'/OUTPUT/metrics/autos_owned.csv')
        tm_travel_cost_df = pd.read_csv(tm_run_location+'/OUTPUT/core_summaries/TravelCost.csv')
        tm_auto_times_df = pd.read_csv(tm_run_location+'/OUTPUT/metrics/auto_times.csv',sep=",")#, index_col=[0,1])
        tm_loaded_network_df = pd.read_csv(tm_run_location+'/OUTPUT/avgload5period.csv')
        tm_loaded_network_df = tm_loaded_network_df.rename(columns=lambda x: x.strip())
        # ----merging df that has the list of minor segments with loaded network - for corridor analysis
        tm_loaded_network_df['a_b'] = tm_loaded_network_df['a'].astype(str) + "_" + tm_loaded_network_df['b'].astype(str)
        tm_loaded_network_df = tm_loaded_network_df.merge(minor_links_df, on='a_b', how='left')
        
        if ODTRAVELTIME_FILENAME == "ODTravelTime_byModeTimeperiod_reduced_file.csv":
            # import network links file from reduced dbf as a dataframe to merge with loaded network and get toll rates
            network_links_dbf = pd.read_csv(tm_run_location + '\\OUTPUT\\shapefile\\network_links_reduced_file.csv')
        else:
            # addding back original code for simplicity of steps to update the tableau workbook (at the cost of run time)
            input_file = tm_run_location + '\\OUTPUT\\shapefile\\network_links.DBF'
            LOGGER.info("Reading {}".format(input_file))
            dbf = simpledbf.Dbf5(input_file)
            network_links_dbf = dbf.to_dataframe()
            LOGGER.debug("network_links_dbf:\n{}".format(network_links_dbf))
            network_links_dbf['a_b'] = network_links_dbf['A'].astype(str) + "_" + network_links_dbf['B'].astype(str)
     
        tm_loaded_network_df = tm_loaded_network_df.copy().merge(network_links_dbf.copy(), on='a_b', how='left')

        # TODO: why?
        # load collisionLookup table
        if tm_run_id == '2035_TM152_NGF_NP07_Path4_02':
          collision_rates_df = pd.read_csv(tm_run_location + '/INPUT_032123_160659/metrics/collisionLookup.csv')
        # elif tm_run_id == '2035_TM152_NGF_NP07_Path3a_05':
        #   collision_rates_df = pd.read_csv(tm_run_location + '/INPUT_033023_ 92607/metrics/collisionLookup.csv')
        else:
          collision_rates_df = pd.read_csv(tm_run_location + '/INPUT/metrics/collisionLookup.csv')

        # load vmt_vht_metrics.csv for vmt calc
        tm_vmt_metrics_df = pd.read_csv(tm_run_location + '/OUTPUT/metrics/vmt_vht_metrics.csv', sep=",", index_col=[0,1])
        # load transit_times_by_mode_income.csv
        tm_transit_times_df = pd.read_csv(tm_run_location + '/OUTPUT/metrics/transit_times_by_mode_income.csv', sep=",", index_col=[0,1])
        # load VehicleMilesTraveled_households.csv
        vmt_hh_df = pd.read_csv(tm_run_location+'/OUTPUT/core_summaries/VehicleMilesTraveled_households.csv')


        # ______load 2015 network to use for speed comparisons in vmt corrections______
        run_2015_location = "L:\\Application\\Model_One\\NextGenFwys\\Scenarios\\2015_TM152_NGF_05"
        runid_2015 = run_2015_location.split('\\')[-1]
        loaded_network_2015_df = pd.read_csv(run_2015_location+'/OUTPUT/avgload5period.csv')
        loaded_network_2015_df = loaded_network_2015_df.rename(columns=lambda x: x.strip())

        # results will be stored here
        # key=grouping1, grouping2, grouping3, tm_run_id, metric_id, top_level|extra|intermediate|final, key, metric_desc, year
        # TODO: convert to pandas.DataFrame with these column headings.  It's far more straightforward.
        metrics_dict = {}
        metrics_df = pd.DataFrame()
        affordable1_metrics_df = calculate_Affordable1_transportation_costs(tm_run_id)
        metrics_df = pd.concat([metrics_df, affordable1_metrics_df])

        # LOGGER.info("@@@@@@@@@@@@@ A1 Done")
        calculate_Affordable2_ratio_time_cost(tm_run_id, year, tm_loaded_network_df, network_links_dbf, metrics_dict)
        # LOGGER.info("@@@@@@@@@@@@@ A2 Done")
        efficient1_metrics_df = calculate_Efficient1_ratio_travel_time(tm_run_id)
        metrics_df = pd.concat([metrics_df, efficient1_metrics_df])
        
        # LOGGER.info("@@@@@@@@@@@@@ E1 Done")
        efficient2_metrics_df = calculate_Efficient2_commute_mode_share(tm_run_id)
        metrics_df = pd.concat([metrics_df, efficient2_metrics_df])
        calculate_Reliable1_change_travel_time(tm_run_id, year, tm_loaded_network_df, metrics_dict)
        # LOGGER.info("@@@@@@@@@@@@@ R1 Done")
        calculate_Reliable2_ratio_peak_nonpeak(tm_run_id, year, tm_loaded_network_df, metrics_dict)
        # LOGGER.info("@@@@@@@@@@@@@ R2 Done")
        calculate_Safe1_fatalities_freeways_nonfreeways(tm_run_id, year, tm_loaded_network_df, metrics_dict)
        # LOGGER.info("@@@@@@@@@@@@@ S1 Done")
        safe2_metrics_df = calculate_Safe2_change_in_vmt(tm_run_id)
        metrics_df = pd.concat([metrics_df, safe2_metrics_df])

        # LOGGER.info("@@@@@@@@@@@@@ S2 Done")

        # run function to calculate top level metrics
        toplevel_metrics_df = calculate_top_level_metrics(tm_run_id, year, tm_vmt_metrics_df, tm_auto_times_df, tm_transit_times_df, tm_loaded_network_df, vmt_hh_df,tm_scen_metrics_df)  # calculate for base run too
        metrics_df = pd.concat([metrics_df, toplevel_metrics_df])

        # _________output table__________
        # TODO: deprecate when all metrics just come through via metrics_df
        metrics_df = pd.concat([metrics_df, metrics_dict_to_df(metrics_dict)])
        # print out table

        metrics_df[METRICS_COLUMNS].to_csv(out_filename, float_format='%.5f', index=False) #, header=False
        LOGGER.info("Wrote {}".format(out_filename))

        # for testing, stop here
        # sys.exit()
