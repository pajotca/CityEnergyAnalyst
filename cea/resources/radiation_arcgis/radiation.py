"""
Solar vertical insolation algorithm based on ArcGIS Solar Analyst
"""
from __future__ import division

from cea.interfaces.arcgis.modules import arcgisscripting
import datetime
import os

from cea.interfaces.arcgis.modules import arcpy
import ephem
import numpy as np
import pandas as pd
from simpledbf import Dbf5
import traceback

from cea.utilities import epwreader

__author__ = "Jimeno A. Fonseca"
__copyright__ = "Copyright 2013, Architecture and Building Systems - ETH Zurich"
__credits__ = ["Jimeno A. Fonseca", "Daren Thomas"]
__license__ = "MIT"
__version__ = "0.1"
__maintainer__ = "Daren Thomas"
__email__ = "cea@arch.ethz.ch"
__status__ = "Production"


def solar_radiation_vertical(locator, path_arcgis_db, latitude, longitude, year, gv, weather_path):
    """
    algorithm to calculate the hourly solar isolation in vertical building surfaces.
    The algorithm is based on the Solar Analyst Engine of ArcGIS 10.
    For more info check the integrated demand model of Fonseca et al. 2015. Appl. energy.

    :param locator: input locator for file paths
    :type locator: cea.inputlocator.InputLocator

    :param path_arcgis_db:  path to default database of Arcgis. E.g.``c:\users\your_name\Documents\Arcgis\Default.gdb``
    :type path_arcgis_db: str

    :param latitude: latitude north  at the centre of the location
    :type latitude: float

    :param longitude: latitude north
    :type longitude: float

    :param year: year of calculation
    :type year: int

    :param gv: global context and constants
    :type gv: cea.globalvar.GlobalVariables

    :param weather_path: path to the weather file
    :type weather_path: str

    :returns: produces ``radiation.csv``, solar radiation file in vertical surfaces of buildings.
    """
    print(weather_path)
    # Set environment settings
    arcpy.env.workspace = path_arcgis_db
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("spatial")

    # local variables
    aspect_slope = "FROM_DEM"
    heightoffset = 1
    simple_cq_shp = locator.get_temporary_file('Simple_CQ_shp.shp')
    simple_context_shp = locator.get_temporary_file('Simple_Context.shp')
    dem_rasterfinal_path = os.path.join(path_arcgis_db, 'DEM_All2')
    observers_path = os.path.join(path_arcgis_db, 'observers')
    data_factors_boundaries_csv = locator.get_temporary_file('DataFactorsBoundaries.csv')
    data_factors_centroids_csv = locator.get_temporary_file('DataFactorsCentroids.csv')

    # calculate sunrise
    sunrise = calc_sunrise(range(1, 366), year, longitude, latitude)

    # calcuate daily transmissivity and daily diffusivity
    weather_data = epwreader.epw_reader(weather_path)[['dayofyear', 'exthorrad_Whm2',
                                                       'glohorrad_Whm2', 'difhorrad_Whm2']]
    weather_data['diff'] = weather_data.difhorrad_Whm2 / weather_data.glohorrad_Whm2
    weather_data = weather_data[np.isfinite(weather_data['diff'])]
    T_G_day = np.round(weather_data.groupby(['dayofyear']).mean(), 2)
    T_G_day['diff'] = T_G_day['diff'].replace(1, 0.90)
    T_G_day['trr'] = (1 - T_G_day['diff'])

    # Simplify building's geometry
    elevRaster = arcpy.sa.Raster(locator.get_terrain())
    dem_raster_extent = elevRaster.extent
    arcpy.SimplifyBuilding_cartography(locator.get_building_geometry(), simple_cq_shp,
                                       simplification_tolerance=7, minimum_area=None)
    arcpy.SimplifyBuilding_cartography(locator.get_district(), simple_context_shp,
                                       simplification_tolerance=7, minimum_area=None)

    # # burn buildings into raster
    Burn(simple_context_shp, locator.get_terrain(), dem_rasterfinal_path, locator.get_temporary_folder(), dem_raster_extent, gv)

    # Calculate boundaries of buildings
    CalcBoundaries(simple_cq_shp, locator.get_temporary_folder(), path_arcgis_db,
                  data_factors_centroids_csv, data_factors_boundaries_csv, gv)

    # calculate observers_path
    CalcObservers(simple_cq_shp, observers_path, data_factors_boundaries_csv, path_arcgis_db, gv)

    # Calculate radiation
    CalcRadiationAllDays(T_G_day, aspect_slope, dem_rasterfinal_path, heightoffset, latitude, locator,
                         observers_path, path_arcgis_db)

    gv.log('complete raw radiation files')

    sunny_hours_of_year = calculate_sunny_hours_of_year(locator, sunrise)

    gv.log('complete transformation radiation files')

    # Assign radiation to every surface of the buildings
    Data_radiation = CalcRadiationSurfaces(observers_path, data_factors_centroids_csv, sunny_hours_of_year,
                                           locator.get_temporary_folder(), path_arcgis_db)

    # get solar insolation @ daren: this is a A BOTTLE NECK
    CalcIncidentRadiation(Data_radiation, locator.get_radiation(), locator.get_surface_properties(), gv)
    gv.log('done')


def CalcRadiationAllDays(T_G_day, aspect_slope, dem_rasterfinal_path, heightoffset, latitude, locator,
                         observers_path, path_arcgis_db):

    # let's just be sure this is set
    arcpy.env.workspace = path_arcgis_db
    arcpy.env.overwriteOutput = True
    arcpy.CheckOutExtension("spatial")

    T_G_day_path = locator.get_temporary_file('T_G_day.pickle')
    T_G_day.to_pickle(T_G_day_path)

    temporary_folder = locator.get_temporary_folder()
    T_G_day = pd.read_pickle(T_G_day_path)
    for day in range(1, 366):
        CalcRadiation(day, dem_rasterfinal_path, observers_path, T_G_day, latitude,
                      temporary_folder, aspect_slope, heightoffset, path_arcgis_db)


def calculate_sunny_hours_of_year(locator, sunrise):
    # run the transformation of files appending all and adding non-sunshine hours
    temporary_folder = locator.get_temporary_folder()
    result_file_path = os.path.join(temporary_folder, 'sunny_hours_of_year.pickle')

    import multiprocessing
    process = multiprocessing.Process(target=_calculate_sunny_hours_of_year,
                                      args=(sunrise, temporary_folder, result_file_path))
    process.start()
    process.join()  ## block until process terminates

    sunny_hours_of_year = pd.read_pickle(result_file_path)
    return sunny_hours_of_year


def _calculate_sunny_hours_of_year(sunrise, temporary_folder, result_file_path):
    """Run this code in separate process to avoid MemoryError of #661"""
    sunny_hours_per_day = []
    for day in range(1, 366):
        result = calculate_sunny_hours_of_day(day, sunrise, temporary_folder)
        result = result.apply(pd.to_numeric, downcast='integer')
        sunny_hours_per_day.append(result)
    sunny_hours_of_year = sunny_hours_per_day[0]
    for df in sunny_hours_per_day[1:]:
        for column in df.columns:
            if column.startswith('T'):
                sunny_hours_of_year[column] = df[column].copy()
                # sunny_hours_of_year = sunny_hours_of_year.merge(df, on='ID', how='outer')
    sunny_hours_of_year = sunny_hours_of_year.fillna(value=0)
    sunny_hours_of_year.to_pickle(result_file_path)
    return None


def CalcIncidentRadiation(radiation, path_radiation_year_final, surface_properties, gv):

    # export surfaces properties
    # radiation['Awall_all'] = radiation['Shape_Leng'] * radiation['FactorShade'] * radiation['Freeheight']
    radiation = calculate_wall_areas(radiation)
    radiation[['Name', 'Freeheight', 'FactorShade', 'height_ag', 'Shape_Leng', 'Awall_all']].to_csv(surface_properties,
                                                                                                    index=False)
    gv.log('saved surface properties to disk')

    # Import Radiation table and compute the Irradiation in W in every building's surface
    hours_in_year = 8760
    column_names = ['T%i' % (i + 1) for i in range(hours_in_year)]
    for column in column_names:
        # transform all the points of solar radiation into Wh
        radiation[column] = radiation[column] * radiation['Awall_all']

    # sum up radiation load per building
    # NOTE: this looks like an ugly hack because it is: in order to work around a pandas MemoryError, we group/sum the
    # columns individually...
    grouped_data_frames = {}
    for column in column_names:
        df = pd.DataFrame(data={'Name': radiation['Name'],
                                column: radiation[column]})
        grouped_data_frames[column] = df.groupby(by='Name').sum()
    radiation_load = pd.DataFrame(index=grouped_data_frames.values()[0].index)
    for column in column_names:
        radiation_load[column] = grouped_data_frames[column][column]

    incident_radiation = np.round(radiation_load[column_names], 2)
    incident_radiation.to_csv(path_radiation_year_final)

    return  # total solar radiation in areas exposed to radiation in Watts


def calculate_wall_areas(radiation):
    """Calculate Awall_all in radiation as the multiplication ``Shape_Leng * FactorShade * Freeheight``
    Uses a subprocess to get around a MemoryError we are having (might have to do with conflicts with ArcGIS numpy?)
    """
    print('pickling radation dataframe to temp folder')
    radiation_pickle_path = os.path.expandvars(r'$temp\radiation.pickle')
    radiation.to_pickle(radiation_pickle_path)

    import multiprocessing
    process = multiprocessing.Process(target=_calculate_wall_areas_subprocess, args=(radiation_pickle_path,))
    process.start()
    process.join()  ## block until process terminates

    del radiation
    import gc
    gc.collect()
    radiation = pd.read_pickle(radiation_pickle_path)
    return radiation


def _calculate_wall_areas_subprocess(radiation_pickle_path):
    """subprocess for calculating wall areas using multiprocessing. the data is passed in the pickled
    dataframe ``radiation_pickle_path``"""

    # use a temporary dataframe for calculations to avoid MemoryError (see #661)
    radiation = pd.read_pickle(radiation_pickle_path)
    radiation.loc[:, 'Awall_all'] = radiation['Shape_Leng'] * radiation['FactorShade'] * radiation['Freeheight']
    radiation.to_pickle(radiation_pickle_path)


def CalcRadiationSurfaces(observers_path, DataFactorsCentroids, Radiationtable, temporary_folder, path_arcgis_db):
    # local variables
    CQSegments_centroid = os.path.join(path_arcgis_db, 'CQSegmentCentro')
    Outjoin = os.path.join(path_arcgis_db, 'Join')
    CQSegments = os.path.join(path_arcgis_db, 'CQSegment')
    OutTable = 'CentroidsIDobserver.dbf'
    # Create Join of features Observers and CQ_sementscentroids to
    # assign Names and IDS of observers (field TARGET_FID) to the centroids of the lines of the buildings,
    # then create a table to import as a Dataframe
    arcpy.SpatialJoin_analysis(CQSegments_centroid, observers_path, Outjoin, "JOIN_ONE_TO_ONE", "KEEP_ALL",
                               match_option="CLOSEST", search_radius="10 METERS")
    arcpy.JoinField_management(Outjoin, 'OBJECTID', CQSegments, 'OBJECTID')  # add the lenghts of the Lines to the File
    arcpy.TableToTable_conversion(Outjoin, temporary_folder, OutTable)

    # ORIG_FID represents the points in the segments of the simplified shape of the building
    # ORIG_FID_1 is the observers ID
    Centroids_ID_observers0_dbf5 = Dbf5(os.path.join(temporary_folder, OutTable)).to_dataframe()
    Centroids_ID_observers_dbf5 = Centroids_ID_observers0_dbf5[
        ['Name', 'height_ag', 'ORIG_FID', 'ORIG_FID_1', 'Shape_Leng']]
    Centroids_ID_observers_dbf5.rename(columns={'ORIG_FID_1': 'ID'}, inplace=True)

    # Create a Join of the Centroid_ID_observers and Datacentroids in the Second Chapter to get values of surfaces Shaded.
    Datacentroids = pd.read_csv(DataFactorsCentroids)
    DataCentroidsFull = pd.merge(Centroids_ID_observers_dbf5, Datacentroids, left_on='ORIG_FID', right_on='ORIG_FID')

    # Read again the radiation table and merge values with the Centroid_ID_observers under the field ID in Radiationtable and 'ORIG_ID' in Centroids...
    DataRadiation = pd.merge(left=DataCentroidsFull, right=Radiationtable, left_on='ID', right_on='ID')

    return DataRadiation


def calculate_sunny_hours_of_day(day, sunrise, temporary_folder):
    """
    :param day:
    :type day: int
    :param sunrise: what is this? seems to be a list of sunrise times, but for the ecocampus case, I get a list of
                    ints like 22 and 23... that can't be right, right?
    :type sunrise: list[int]
    :param temporary_folder: path to temporary folder with the radiations per day
    :return:
    """
    radiation_sunnyhours = np.round(Dbf5(os.path.join(temporary_folder, 'Day_%(day)i.dbf' % locals())).to_dataframe(),
                                    2)

    # Obtain the number of points modeled to do the iterations
    radiation_sunnyhours['ID'] = 0
    radiation_sunnyhours['ID'] = range(1, radiation_sunnyhours.ID.count() + 1)

    # Table with empty values with the same range as the points.
    Table = pd.DataFrame.copy(radiation_sunnyhours)
    listtimes = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T9', 'T10', 'T11', 'T12', 'T13', 'T14', 'T15', 'T16',
                 'T17', 'T18', 'T19', 'T20', 'T21', 'T22', 'T23', 'T24']
    for x in listtimes:
        Table[x] = 0
    Table.drop('T0', axis=1, inplace=True)

    # Counter of Columns in the Initial Table
    Counter = radiation_sunnyhours.count(1)[0]
    values = Counter - 1
    # Condition to take into account daysavingtime in Switzerland as the radiation data in ArcGIS is calculated for 2013.
    if 90 <= day < 300:
        D = 1
    else:
        D = 0
    # Calculation of Sunrise time
    Sunrise_time = sunrise[day - 1]
    # Calculation of table
    for x in range(values):
        Hour = int(Sunrise_time) + int(D) + int(x)
        Table['T' + str(Hour)] = radiation_sunnyhours['T' + str(x)]

    # rename the table for every T to get in 1 to 8760 hours.
    if day <= 1:
        name = 1
    else:
        name = int(day - 1) * 24 + 1

    Table.rename(
        columns={'T1': 'T' + str(name), 'T2': 'T' + str(name + 1), 'T3': 'T' + str(name + 2), 'T4': 'T' + str(name + 3),
                 'T5': 'T' + str(name + 4),
                 'T6': 'T' + str(name + 5), 'T7': 'T' + str(name + 6), 'T8': 'T' + str(name + 7),
                 'T9': 'T' + str(name + 8), 'T10': 'T' + str(name + 9),
                 'T11': 'T' + str(name + 10), 'T12': 'T' + str(name + 11), 'T13': 'T' + str(name + 12),
                 'T14': 'T' + str(name + 13), 'T15': 'T' + str(name + 14),
                 'T16': 'T' + str(name + 15), 'T17': 'T' + str(name + 16), 'T18': 'T' + str(name + 17),
                 'T19': 'T' + str(name + 18), 'T20': 'T' + str(name + 19),
                 'T21': 'T' + str(name + 20), 'T22': 'T' + str(name + 21), 'T23': 'T' + str(name + 22),
                 'T24': 'T' + str(name + 23), 'ID': 'ID'}, inplace=True)

    return Table


def CalcRadiation(day, in_surface_raster, in_points_feature, T_G_day, latitude, locationtemp1, aspect_slope,
                  heightoffset, path_arcgis_db):
    # Local Variables
    Latitude = str(latitude)
    skySize = '400'  # max 10000
    dayInterval = '1'
    hourInterval = '1'
    calcDirections = '32'
    zenithDivisions = '600'  # max 1200cor hlaf the skysize
    azimuthDivisions = '80'  # max 160
    diffuseProp = str(T_G_day.loc[day, 'diff'])
    transmittivity = str(T_G_day.loc[day, 'trr'])
    heightoffset = str(heightoffset)
    global_radiation = locationtemp1 + '\\' + 'Day_' + str(day) + '.shp'
    timeConfig = 'WithinDay    ' + str(day) + ', 0, 24'

    # Run the extension of arcgis, retry max_retries times before giving up...
    max_retries = 3
    for _ in range(max_retries):
        try:
            _CalcRadiation(Latitude, aspect_slope, azimuthDivisions, calcDirections, dayInterval, diffuseProp, global_radiation,
                           heightoffset, hourInterval, in_points_feature, in_surface_raster, skySize, timeConfig,
                           transmittivity, zenithDivisions, path_arcgis_db)
            print('complete calculating radiation of day No. %(day)i' % locals())
            return arcpy.GetMessages()
        except:
            print(traceback.format_exc())
    raise AssertionError('_CalcRadiation failed %(max_retries)i times... giving up!' % locals())



def _CalcRadiation(Latitude, aspect_slope, azimuthDivisions, calcDirections, dayInterval, diffuseProp,
                   global_radiation, heightoffset, hourInterval, in_points_feature, in_surface_raster, skySize,
                   timeConfig, transmittivity, zenithDivisions, path_arcgis_db):
    """Splitting off a possibly problematic piece of code to a separate process..."""
    try:
        # os.chdir(path_arcgis_db)  # make sure this process is in a writeable directory
        arcpy.env.workspace = path_arcgis_db
        arcpy.env.overwriteOutput = True
        arcpy.CheckOutExtension("spatial")
        arcpy.sa.PointsSolarRadiation(in_surface_raster, in_points_feature, global_radiation, heightoffset,
                                      Latitude, skySize, timeConfig, dayInterval, hourInterval, "INTERVAL", "1",
                                      aspect_slope,
                                      calcDirections, zenithDivisions, azimuthDivisions, "STANDARD_OVERCAST_SKY",
                                      diffuseProp, transmittivity, "#", "#", "#")
    except:
        print(traceback.format_exc())
        raise


def CalcObservers(Simple_CQ, Observers, DataFactorsBoundaries, locationtemporal2, gv):
    # local variables
    Buffer_CQ = locationtemporal2 + '\\' + 'BufferCQ'
    temporal_lines = locationtemporal2 + '\\' + 'lines'
    Points = locationtemporal2 + '\\' + 'Points'
    AggregatedBuffer = locationtemporal2 + '\\' + 'BufferAggregated'
    temporal_lines3 = locationtemporal2 + '\\' + 'lines3'
    Points3 = locationtemporal2 + '\\' + 'Points3'
    Points3Updated = locationtemporal2 + '\\' + 'Points3Updated'
    EraseObservers = locationtemporal2 + '\\' + 'eraseobservers'
    Observers0 = locationtemporal2 + '\\' + 'observers0'
    NonoverlappingBuildings = locationtemporal2 + '\\' + 'Non_overlap'
    templines = locationtemporal2 + '\\' + 'templines'
    templines2 = locationtemporal2 + '\\' + 'templines2'
    Buffer_CQ0 = locationtemporal2 + '\\' + 'Buffer_CQ0'
    Buffer_CQ = locationtemporal2 + '\\' + 'Buffer_CQ'
    Buffer_CQ1 = locationtemporal2 + '\\' + 'Buffer_CQ1'
    Simple_CQcopy = locationtemporal2 + '\\' + 'Simple_CQcopy'
    # First increase the boundaries in 2m of each surface in the community to
    # analyze- this will avoid that the observers overlap the buildings and Simplify
    # the community vertices to only create 1 point per surface

    arcpy.CopyFeatures_management(Simple_CQ, Simple_CQcopy)
    # Make Square-like buffers
    arcpy.PolygonToLine_management(Simple_CQcopy, templines, "IGNORE_NEIGHBORS")
    arcpy.SplitLine_management(templines, templines2)
    arcpy.Buffer_analysis(templines2, Buffer_CQ0, "0.75 Meters", "FULL", "FLAT", "NONE", "#")
    arcpy.Append_management(Simple_CQcopy, Buffer_CQ0, "NO_TEST")
    arcpy.Dissolve_management(Buffer_CQ0, Buffer_CQ1, "Name", "#", "SINGLE_PART", "DISSOLVE_LINES")
    arcpy.SimplifyBuilding_cartography(Buffer_CQ1, Buffer_CQ, simplification_tolerance=8, minimum_area=None)

    # arcpy.Buffer_analysis(Simple_CQ,Buffer_CQ,buffer_distance_or_field=1, line_end_type='FLAT') # buffer with a flat finishing
    # arcpy.Generalize_edit(Buffer_CQ,"2 METERS")

    # Transform all polygons of the simplified areas to observation points
    arcpy.SplitLine_management(Buffer_CQ, temporal_lines)
    arcpy.FeatureVerticesToPoints_management(temporal_lines, Points,
                                             'MID')  # Second the transformation of Lines to a mid point

    # Join all the polygons to get extra vertices, make lines and then get points.
    # these points should be added to the original observation points
    arcpy.AggregatePolygons_cartography(Buffer_CQ, AggregatedBuffer, "0.5 Meters", "0 SquareMeters", "0 SquareMeters",
                                        "ORTHOGONAL")  # agregate polygons
    arcpy.SplitLine_management(AggregatedBuffer, temporal_lines3)  # make lines
    arcpy.FeatureVerticesToPoints_management(temporal_lines3, Points3, 'MID')  # create extra points

    # add information to Points3 about their buildings
    arcpy.SpatialJoin_analysis(Points3, Buffer_CQ, Points3Updated, "JOIN_ONE_TO_ONE", "KEEP_ALL",
                               match_option="CLOSEST", search_radius="5 METERS")
    arcpy.Erase_analysis(Points3Updated, Points, EraseObservers, "2 Meters")  # erase overlaping points
    arcpy.Merge_management([Points, EraseObservers], Observers0)  # erase overlaping points

    #  Eliminate Observation points above roofs of the highest surfaces(a trick to make the
    # Import Overlaptable from function CalcBoundaries containing the data about buildings overlaping, eliminate duplicades, chose only those ones no overlaped and reindex
    DataNear = pd.read_csv(DataFactorsBoundaries)
    CleanDataNear = DataNear[DataNear['FactorShade'] == 1]
    CleanDataNear.drop_duplicates(subset='Name_x', inplace=True)
    CleanDataNear.reset_index(inplace=True)
    rows = CleanDataNear.Name_x.count()
    if rows > 0: #there are overlapping buildings
        for row in range(rows):
            Field = "Name"  # select field where the name exists to iterate
            Value = CleanDataNear.loc[row, 'Name_x']  # set the value or name of the City quarter
            Where_clausule = '''''' + '"' + Field + '"' + "=" + "\'" + str(
                Value) + "\'" + ''''''  # strange writing to introduce in ArcGIS
            if row == 0:
                arcpy.MakeFeatureLayer_management(Simple_CQ, 'Simple_lyr')
                arcpy.SelectLayerByAttribute_management('Simple_lyr', "NEW_SELECTION", Where_clausule)
            else:
                arcpy.SelectLayerByAttribute_management('Simple_lyr', "ADD_TO_SELECTION", Where_clausule)

            arcpy.CopyFeatures_management('simple_lyr', NonoverlappingBuildings)
        arcpy.ErasePoint_edit(Observers0, NonoverlappingBuildings, "INSIDE")

    arcpy.CopyFeatures_management(Observers0, Observers)  # copy features to reset the OBJECTID
    with arcpy.da.UpdateCursor(Observers, ["OBJECTID", "ORIG_FID"]) as cursor:
        for row in cursor:
            row[1] = row[0]
            cursor.updateRow(row)
    gv.log('complete calculating observers')
    return arcpy.GetMessages()


def CalcBoundaries(Simple_CQ, locationtemp1, locationtemp2, DataFactorsCentroids, DataFactorsBoundaries, gv):
    # local variables
    NearTable = locationtemp1 + '\\' + 'NearTable.dbf'
    CQLines = locationtemp2 + '\\' + '\CQLines'
    CQVertices = locationtemp2 + '\\' + 'CQVertices'
    CQSegments = locationtemp2 + '\\' + 'CQSegment'
    CQSegments_centroid = locationtemp2 + '\\' + 'CQSegmentCentro'
    centroidsTable_name = 'CentroidCQdata.dbf'
    centroidsTable = locationtemp1 + '\\' + centroidsTable_name
    Overlaptable = locationtemp1 + '\\' + 'overlapingTable.csv'

    # Create points in the centroid of segment line and table with near features:
    # indentifying for each segment of line of building A the segment of line of building B in common.
    arcpy.FeatureToLine_management(Simple_CQ, CQLines)
    arcpy.FeatureVerticesToPoints_management(Simple_CQ, CQVertices, 'ALL')
    arcpy.SplitLineAtPoint_management(CQLines, CQVertices, CQSegments, '2 METERS')
    arcpy.FeatureVerticesToPoints_management(CQSegments, CQSegments_centroid, 'MID')
    arcpy.GenerateNearTable_analysis(CQSegments_centroid, CQSegments_centroid, NearTable, "1 Meters", "NO_LOCATION",
                                     "NO_ANGLE", "CLOSEST", "0")

    # Import the table with NearMatches
    NearMatches = Dbf5(NearTable).to_dataframe()

    # Import the table with attributes of the centroids of the Segments
    arcpy.TableToTable_conversion(CQSegments_centroid, locationtemp1, centroidsTable_name)
    DataCentroids0 = Dbf5(centroidsTable).to_dataframe()
    DataCentroids = DataCentroids0[['Name', 'height_ag', 'ORIG_FID']]

    # CreateJoin to Assign a Factor to every Centroid of the lines,
    FirstJoin = pd.merge(NearMatches, DataCentroids, left_on='IN_FID', right_on='ORIG_FID')
    SecondaryJoin = pd.merge(FirstJoin, DataCentroids, left_on='NEAR_FID', right_on='ORIG_FID')

    # delete matches within the same polygon Name (it can happen that lines are too close one to the other)
    # also delete matches with a distance of more than 20 cm making room for mistakes during the simplicfication of buildings but avoiding deleten boundaries
    rows = SecondaryJoin.IN_FID.count()
    for row in range(rows):
        if SecondaryJoin.loc[row, 'Name_x'] == SecondaryJoin.loc[row, 'Name_y'] or SecondaryJoin.loc[
            row, 'NEAR_DIST'] > 0.2:
            SecondaryJoin = SecondaryJoin.drop(row)
    SecondaryJoin.reset_index(inplace=True)

    # FactorShade = 0 if the line exist in a building totally covered by another one, and Freeheight is equal to the height of the line
    # that is not obstructed by the other building
    rows = SecondaryJoin.IN_FID.count()
    SecondaryJoin['FactorShade'] = 0
    SecondaryJoin['Freeheight'] = 0
    for row in range(rows):
        if SecondaryJoin.loc[row, 'height_ag_x'] <= SecondaryJoin.loc[row, 'height_ag_y']:
            SecondaryJoin.loc[row, 'FactorShade'] = 0
            SecondaryJoin.loc[row, 'Freeheight'] = 0
        elif SecondaryJoin.loc[row, 'height_ag_x'] > SecondaryJoin.loc[row, 'height_ag_y'] and SecondaryJoin.loc[
            row, 'height_ag_x'] - 1 <= SecondaryJoin.loc[row, 'height_ag_y']:
            SecondaryJoin.loc[row, 'FactorShade'] = 0
        else:
            SecondaryJoin.loc[row, 'FactorShade'] = 1
            SecondaryJoin.loc[row, 'Freeheight'] = abs(
                SecondaryJoin.loc[row, 'height_ag_y'] - SecondaryJoin.loc[row, 'height_ag_x'])

    # Create and export Secondary Join with results, it will be Useful for the function CalcObservers
    SecondaryJoin.to_csv(DataFactorsBoundaries, index=False)

    # Update table Datacentroids with the Fields Freeheight and Factor Shade. for those buildings without
    # shading boundaries these factors are equal to 1 and the field 'height' respectively.
    pd.options.mode.chained_assignment = None
    DataCentroids['FactorShade'] = 1
    DataCentroids['Freeheight'] = DataCentroids.height_ag
    Results = DataCentroids.merge(SecondaryJoin, left_on='ORIG_FID', right_on='ORIG_FID_x', how='outer')
    Results.FactorShade_y.fillna(Results['FactorShade_x'], inplace=True)
    Results.Freeheight_y.fillna(Results['Freeheight_x'], inplace=True)
    Results.rename(columns={'FactorShade_y': 'FactorShade', 'Freeheight_y': 'Freeheight'}, inplace=True)
    FinalDataCentroids = pd.DataFrame(Results, columns={'ORIG_FID', 'height', 'FactorShade', 'Freeheight'})

    FinalDataCentroids.to_csv(DataFactorsCentroids, index=False)
    gv.log('complete calculating boundaries')
    return arcpy.GetMessages()


def Burn(Buildings, DEM, DEMfinal, locationtemp1, DEM_extent, gv):
    # Create a raster with all the buildings
    Outraster = locationtemp1 + '\\' + 'AllRaster'
    arcpy.env.extent = DEM_extent  # These coordinates are extracted from the environment settings/once the DEM raster is selected directly in ArcGIS,
    arcpy.FeatureToRaster_conversion(Buildings, 'height_ag', Outraster,
                                     '0.5')  # creating raster of the footprints of the buildings

    # Clear non values and add all the Buildings to the DEM
    OutNullRas = arcpy.sa.IsNull(Outraster)  # identify noData Locations
    Output = arcpy.sa.Con(OutNullRas == 1, 0, Outraster)
    RadiationDEM = arcpy.sa.Raster(DEM) + Output
    RadiationDEM.save(DEMfinal)
    gv.log('complete burning buildings into raster')

    return arcpy.GetMessages()


def calc_sunrise(sunrise, year_to_simulate, longitude, latitude):
    o = ephem.Observer()
    o.lat = str(latitude)
    o.long = str(longitude)
    s = ephem.Sun()
    for day in range(1, 366):  # Calculated according to NOAA website
        o.date = datetime.datetime(year_to_simulate, 1, 1) + datetime.timedelta(day - 1)
        next_event = o.next_rising(s)
        sunrise[day - 1] = next_event.datetime().hour
    print('complete calculating sunrise')
    return sunrise


def get_latitude(scenario_path):
    import fiona
    import cea.inputlocator
    with fiona.open(cea.inputlocator.InputLocator(scenario_path).get_building_geometry()) as shp:
        lat = shp.crs['lat_0']
    return lat

def get_longitude(scenario_path):
    import fiona
    import cea.inputlocator
    with fiona.open(cea.inputlocator.InputLocator(scenario_path).get_building_geometry()) as shp:
        lon = shp.crs['lon_0']
    return lon


def run_as_script(scenario_path=None, weather_path=None, latitude=None, longitude=None, year=None):
    import cea.globalvar
    import cea.inputlocator
    gv = cea.globalvar.GlobalVariables()
    if scenario_path is None:
        scenario_path = gv.scenario_reference
    locator = cea.inputlocator.InputLocator(scenario_path)
    if weather_path is None:
        weather_path = locator.get_default_weather()
    if latitude is None:
        latitude = get_latitude(scenario_path)
    if longitude is None:
        longitude = get_longitude(scenario_path)
    if year is None:
        year = 2016
    path_default_arcgis_db = os.path.expanduser(os.path.join('~', 'Documents', 'ArcGIS', 'Default.gdb'))

    solar_radiation_vertical(locator=locator, path_arcgis_db=path_default_arcgis_db,
                             latitude=latitude, longitude=longitude, year=year, gv=gv,
                             weather_path=weather_path)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--scenario', help='Path to the scenario folder')
    parser.add_argument('-w', '--weather', help='Path to the weather file')
    parser.add_argument('--latitude', help='Latitutde', default=47.1628017306431)
    parser.add_argument('--longitude', help='Longitude', default=8.31)
    parser.add_argument('--year', help='Year', default=2010)
    args = parser.parse_args()

    run_as_script(scenario_path=args.scenario, weather_path=args.weather, latitude=args.latitude,
                  longitude=args.longitude, year=args.year)
