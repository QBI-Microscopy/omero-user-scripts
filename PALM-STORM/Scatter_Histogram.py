#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import time
import numpy as np
import pandas as pd
import tempfile
import itertools
from math import ceil

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *


FILE_TYPES = {
               'palmtracer'     :{
                                 'numColumns': 9, 
                                 'name': 'palmtracer', 
                                 'frame': 'time', 
                                 'header_row': None, 
                                 'x_col': 'X position (pixel)', 
                                 'y_col': 'Y position (pixel)',
                                 'z_col': None                                 
                                 },
               'localizer'     :{
                                 'numColumns': 12, 
                                 'name': 'localizer', 
                                 'frame': 'First frame',
                                 'intensity': 'Integrated intensity', 
                                 'precision': 'Fitted PSF standard deviation', 
                                 'zprecision': None, 
                                 'header_row': 5, 
                                 'x_col': 'X position (pixel)', 
                                 'y_col': 'Y position (pixel)',
                                 'z_col': None                                 
                                 },
               'quickpalm'     :{
                                 'numColumns': 15, 
                                 'name': 'quickpalm', 
                                 'frame': 'Frame Number',
                                 'intensity': 'Intensity', 
                                 'precision': None, 
                                 'zprecision': None, 
                                 'header_row': 0, 
                                 'x_col': 'X (px)', 
                                 'y_col': 'Y (px)',
                                 'z_col': None                                 
                                 },               
               'zeiss2D'       :{
                                 'numColumns': 13, 
                                 'name': 'zeiss2D', 
                                 'frame': 'First Frame',
                                 'intensity': 'Number Photons', 
                                 'precision': 'Precision [nm]', 
                                 'zprecision': None, 
                                 'header_row': 0, 
                                 'x_col': 'Position X [nm]', 
                                 'y_col': 'Position Y [nm]',
                                 'z_col': None
                                  },
               'zeiss3D'       :{
                                 'numColumns': 14, 
                                 'name': 'zeiss2D', 
                                 'frame': 'First Frame',
                                 'intensity': 'Number Photons', 
                                 'precision': 'Precision [nm]', 
                                 'zprecision': 'Precision Z [nm]', 
                                 'header_row': 0, 
                                 'x_col': 'Position X [nm]', 
                                 'y_col': 'Position Y [nm]',
                                 'z_col': 'Position Z [nm]'
                                  }
}
PATH = tempfile.mkdtemp(prefix='downloads')

def get_rectangles(conn, imageId):
    """
    Returns a list of (x, y, width, height, zStart, zStop, tStart, tStop)
    of each rectange ROI in the image scaled to nm
    
    @param conn:    the BlitzGateWay connection
    @param imageId: the id on the server of the image that holds the ROIs
    """

    rois = []

    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)
    im = conn.getObject('Image',imageId)
    pixels = im.getPrimaryPixels()
    # note pixel sizes (if available) to set for the new images
    physX = pixels.physicalSizeX.getValue()*1000.0
    physY = pixels.physicalSizeY.getValue()*1000.0
    
    for roi in result.rois:
        zStart = None
        zEnd = 0
        tStart = None
        tEnd = 0
        x = None
        for shape in roi.copyShapes():
            if type(shape) == omero.model.RectI:
                # check t range and z range for every rectangle
                t = shape.getTheT().getValue()
                z = shape.getTheZ().getValue()
                if tStart is None:
                    tStart = t
                if zStart is None:
                    zStart = z
                tStart = min(t, tStart)
                tEnd = max(t, tEnd)
                zStart = min(z, zStart)
                zEnd = max(z, zEnd)
                if x is None: # get x, y, width, height for first rect only
                    x = int(shape.getX().getValue())
                    y = int(shape.getY().getValue())
                    width = int(shape.getWidth().getValue())
                    height = int(shape.getHeight().getValue())
        # if we have found any rectangles at all...
        if zStart is not None:
            rois.append((x*physX, y*physY, width*physX, height*physY, zStart, zEnd, tStart, tEnd))

    return rois

def get_all_locs_in_chan(all_data,chan=0,chancol=None):
    """
    The dataset could contain two channels (Zeiss data) in which case there will be a
    column which indicates the channel number and the localisations in each channel are
    stacked vertically in the file
    
    @param all_data: all the data read from the localisations file
    @param col:      which coordinate column in all_data we are currently extracting
    @param chan:     the channel we are currently extracting
    @param chancol:  the column in all_data where we will find the channel assignment
    """        

    if chancol:
        coords = all_data[all_data[chancol] == chan]
    else:
        coords = all_data
    return coords
    
def get_all_locs(all_data,sizeC,file_type,nm_per_pixel):
    """
    Returns the xy coordinates from the input data in a numpy array
    
    @param all_data: all the data read from the localisations file
    @param sizeC:    how many channels in the dataset
    @param chancol:  which column in all_data where we will find the channel assignment
    @param pix_size: the size of the pixel in the original data --> need for converting localisations
                     to nm. defaults to 1 for Zeiss data which is already in nm
    """    
    if sizeC > 1:
        chancol = file_type['chan_col']
    else:
        chancol = None
    xcol = file_type['x_col']
    ycol = file_type['y_col']
    frame = file_type['frame']
    coords = [] 
    print 'sizeC:',sizeC
    for c in range(sizeC):
        inchan = get_all_locs_in_chan(all_data,c,chancol)
        x = pd.DataFrame(inchan[xcol]*nm_per_pixel,columns=[xcol])
        y = pd.DataFrame(inchan[ycol]*nm_per_pixel,columns=[ycol])
        t = pd.DataFrame(inchan[frame],columns=[frame])
        reduced = pd.concat([x,y,t],join='outer',axis=1)        
        coords.append(reduced)   
    return coords
                    
def parse_sr_data(path,file_type,pix_size=95):
    """
    Parses all the data in the file being processed,
    and returns the xy coords in a numpy array
    
    @param path:      the path to the localisations file being read
    @param file_type: what type of dataset are we dealing with (see FILE_TYPES global)
    @param pix_size:  the size of the pixel in the original data --> need for converting localisations
                     to nm. defaults to 1 for Zeiss data which is already in nm
    """
    header_row = file_type['header_row']

    if 'zeiss2chan2D' in file_type['name']:
        sizeC = 2
        chancol = file_type['chan_col']
    else:
        sizeC = 1
        chancol = None   
        
    num_lines = sum(1 for line in open(path))
    s = time.time()
    try:
        with open(path) as t_in:
            data = pd.read_csv(t_in,header=header_row,\
                               sep='\t',engine='c',\
                               skiprows=range(num_lines-50,num_lines),\
                               index_col=False,low_memory=False)  
    except:
        print 'there was a problem parsing localisation data'
        return None
    print 'reading the file took:',time.time()-s,'seconds'
    coords = get_all_locs(data,sizeC,file_type,pix_size)
    return coords        

def download_data(ann):
    """
    Downloads the specified file to and returns the path on the server
    
    @param ann: the file annotation we are downloading
    """ 
    if not os.path.exists(PATH):
        os.makedirs(PATH)
    file_path = os.path.join(PATH, ann.getFile().getName())
    f = open(str(file_path), 'w')
    print "\nDownloading file to", file_path, "..."
    try:
        for chunk in ann.getFileInChunks():
            f.write(chunk)
    finally:
        f.close()
        print "File downloaded!"
    return file_path
    
def delete_downloaded_data(ann):
    """
    Deletes the specified file from its temporary location
    
    @param ann: the file annotation being deleted
    """       
    file_path = os.path.join(PATH, ann.getFile().getName())
    shutil.rmtree(PATH)
    
def get_coords_in_roi(all_coords,roi,file_type):
    """
    Returns the xy coordinates of the rectangular roi being processed
    
    @param all_coords: all xy and potentially z coordinates extracted from localisations file
    @param roi:        we are finding the coordinates inside this region
    @param file_type: what type of dataset are we dealing with (see FILE_TYPES global)    
    """
   
    xstart = roi[0]
    xstop = roi[0]+roi[2]
    ystart = roi[1]
    ystop = roi[1]+roi[3]
    x = file_type['x_col']
    y = file_type['y_col']
    return all_coords[(all_coords[x] > xstart) & (all_coords[x] < xstop)
                      & (all_coords[y] > ystart) & (all_coords[y] < ystop)]
    
def process_data(conn,image,file_type,rectangles,localisations,scalex,scaley,cIndex=0):
    """
    Get the coordinates in the ROI and form histograms of x and y coordinates
    
    @param conn:       the BlitzGateWay connection
    @param image:      the image containing the ROIs to process. the image were the localisations file is attached.
    @param file_type:  what type of dataset are we dealing with (see FILE_TYPES global) 
    @param rectangles: the ROIs scaled to nm
    @param coords:     the xy coordinates of localisations being histogrammed
    @param scalex:     multiplier to pixel size to determine bin size in histograms
    @param scaley:     multiplier to pixel size to determine bin size in histograms
    """    
    message = ""
    pixels = image.getPrimaryPixels()
    # note pixel sizes (if available) to set for the new images
    physX = pixels.getPhysicalSizeX()*1000.0
    physY = pixels.getPhysicalSizeY()*1000.0
    x = file_type['x_col']
    y = file_type['y_col']
    for i,rect in enumerate(rectangles):
        binsx = ceil((rect[2]/physX)/scalex)
        binsy = ceil((rect[3]/physY)/scaley)
        print 'binsx,binsy:',binsx,binsy
        rangex = rect[2]
        rangey = rect[3]
        print 'rangex,rangey:',rangex,rangey

        locs_df = get_coords_in_roi(localisations[cIndex],rect,file_type)
        histx,edgesx = np.histogram(locs_df.loc[:,[x]].values,bins=binsx)
        histy,edgesy = np.histogram(locs_df.loc[:,[y]].values,bins=binsy)
        hist_dataX = np.zeros((histx.shape[0],1))
        hist_dataX[:,0] = histx
        hist_dataY = np.zeros((histy.shape[0],1))
        hist_dataY[:,0] = histy    
                  
        centersx = 0.5*(edgesx[1:]+edgesx[:-1])
        centersy = 0.5*(edgesy[1:]+edgesy[:-1])
        centers_dataX = np.zeros((centersx.shape[0],1))
        centers_dataX[:,0] = centersx
        centers_dataY = np.zeros((centersy.shape[0],1))
        centers_dataY[:,0] = centersy
        
        file_name = "scatter_hist_in_roi_%s.csv" % i
        with file(file_name, 'w') as outfile:
            outfile.write('# scatter histogram data in x-direction\n')
            datax = np.concatenate((centers_dataX,hist_dataX),axis=1)
            np.savetxt(outfile, datax, fmt='%-7.2f', delimiter=',', newline='\n')
            outfile.write('# scatter histogram data in y-direction\n')
            datay = np.concatenate((centers_dataY,hist_dataY),axis=1)
            np.savetxt(outfile, datay, fmt='%-7.2f', delimiter=',', newline='\n')
            
        new_file_ann, faMessage = script_util.createLinkFileAnnotation(
            conn, file_name, image, output="wrote coords file for ROI %s" %i,
            mimetype="text/csv", desc=None)
        message += faMessage
    return message
                            
def run_processing(conn,script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """
    file_anns = []
    message = ""
    
    image_id = script_params['ImageID']
    image = conn.getObject("Image",image_id)
    if not image:
        message = 'Could not find specified image'
        return message
        
    file_id = script_params['AnnotationID']
    ann = conn.getObject("Annotation",file_id)
    if not ann:
        message = 'Could not find specified annotation'
        return message
    
    #other parameters
    if script_params['Convert_coordinates_to_nm']:
        cam_pix_size = script_params['Parent_Image_Pixel_Size']
    else:
        cam_pix_size = 1
        
    scalex = script_params['Scale_for_X_bins']
    scaley = script_params['Scale_for_Y_bins']
        
    file_type = FILE_TYPES[script_params['File_Type']]
     
    path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
    name,ext = os.path.splitext(path_to_ann)
    if ('txt' in ext) or ('csv' in ext):
        path_to_data = download_data(ann)
        coords = parse_sr_data(path_to_data,file_type,cam_pix_size)
        rectangles = get_rectangles(conn,image_id)
        faMessage = process_data(conn,image,file_type,rectangles,coords,scalex,scaley)
    else:
        message = 'file annotation must be txt or csv'
        return message
    # clean up
    delete_downloaded_data(ann)
    
    message = faMessage
    return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Image')]
    
    fileTypes = [k for k in FILE_TYPES.iterkeys()]

    client = scripts.client('Scatter_Histogram.py', """This script searches an attached SR dataset for coords defined by an ROI on a super resolved image. 
It calculates histograms for the x and y coordinates separately for the ROI.
Only use with super resolved images where the physical pixel size is set (CZI or OME-TIFF).
Do not convert Zeiss datasets to nm!""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.Int("ImageID", optional=False, grouping="02",
        description="ID of super resolved image to process"),
        
    scripts.Int("AnnotationID", optional=False, grouping="03",
        description="ID of file to process"),
        
    scripts.String("File_Type", optional=False, grouping="04",
        description="Indicate the type of data being processed", values=fileTypes, default="zeiss2D"),

    scripts.Bool("Convert_coordinates_to_nm", optional=False, grouping="05",
        description="Convert localisation coordinates to nm - DO NOT USE WITH ZEISS DATA", default=False),
                            
    scripts.Int("Parent_Image_Pixel_Size", grouping="05.1",
        description="Convert the localisation coordinates to nm (multiply by parent image pixel size)"),
                            
    scripts.Int("Scale_for_X_bins", grouping="06.1", optional=False, default=2,
        description="Scale the histogram bins in the x-direction by a multiplication factor of the pixel size"),                            

    scripts.Int("Scale_for_Y_bins", grouping="06.2", optional=False, default=2,
        description="Scale the histogram bins in the x-direction by a multiplication factor of the pixel size"), 
                                    
    authors = ["Daniel Matthews", "QBI"],
    institutions = ["University of Queensland"],
    contact = "d.matthews1@uq.edu.au",
    )

    try:

        # process the list of args above.
        scriptParams = {}
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)

        print scriptParams

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)

        # process images in Datasets
        message = run_processing(conn, scriptParams)
        client.setOutput("Message", rstring(message))
        
        #client.setOutput("Message", rstring("No plates created. See 'Error' or 'Info' for details"))
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()
