#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import time
import numpy as np
from random import random
import pandas as pd
from scipy.spatial.distance import cdist
import tempfile

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
from omero.util.temp_files import create_path,remove_path

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
                                  },
               'zeiss2chan2D'  :{
                                 'numColumns': 13, 
                                 'name': 'zeiss2D', 
                                 'frame': 'First Frame',
                                 'intensity': 'Number Photons', 
                                 'precision': 'Precision [nm]', 
                                 'zprecision': None, 
                                 'header_row': 0, 
                                 'x_col': 'Position X [nm]', 
                                 'y_col': 'Position Y [nm]',
                                 'z_col': None,
                                 'chan_col': 'Channel'
                                  },
               'zeiss2chan3D'  :{
                                 'numColumns': 14, 
                                 'name': 'zeiss2D', 
                                 'frame': 'First Frame',
                                 'intensity': 'Number Photons', 
                                 'precision': 'Precision [nm]', 
                                 'zprecision': 'Precision Z [nm]', 
                                 'header_row': 0, 
                                 'x_col': 'Position X [nm]', 
                                 'y_col': 'Position Y [nm]',
                                 'z_col': 'Position Z [nm]',
                                 'chan_col': 'Channel'
                                 }
}
PATH = tempfile.mkdtemp(prefix='downloads')

def create_distance_matrix(pointsA,pointsB):   
    return np.sort(cdist(np.array(pointsA),np.array(pointsB),'euclidean'))

def localisation_density(dataXY,radius):
    dist_mat = create_distance_matrix(dataXY,dataXY)
    N = dataXY.shape[0]
    D = np.zeros(N)
    for i in range(N):
        D[i] = np.count_nonzero(np.where(dist_mat[i,1:]<radius)[0])
    return np.reshape(D,(D.shape[0],1))

def get_rectangles(conn, imageId):
    """
        Returns a list of (x, y, width, height, zStart, zStop, tStart, tStop)
        of each rectange ROI in the image
    """

    rois = []

    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)
    im = conn.getObject('Image',imageId)
    pixels = im.getPrimaryPixels()
    # note pixel sizes (if available) to set for the new images
    physX = pixels.physicalSizeX.getValue()*1000.0 #need this in nm
    physY = pixels.physicalSizeY.getValue()*1000.0 #need this in nm
    print 'physicalSizeX:',physX
    print 'physicalSizeY:',physY
    
    for roi in result.rois:
        zStart = None
        zEnd = 0
        tStart = None
        tEnd = 0
        x = None
        roi_id = roi.getId().getValue()
        for shape in roi.copyShapes():
            if type(shape) == omero.model.PolygonI:
                print shape
                
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
                    x = shape.getX().getValue()
                    y = shape.getY().getValue()
                    width = shape.getWidth().getValue()
                    height = shape.getHeight().getValue()
                    print 'x,y,width,height:',x,y,width,height
        # if we have found any rectangles at all...
        if zStart is not None:
            rois.append((x*physX, y*physY, width*physX, height*physY, zStart, zEnd, tStart, tEnd, roi_id))

    return rois

def get_all_locs_in_chan(all_data,chan=0,chancol=None):

    if chancol:
        coords = all_data[all_data[chancol] == chan]
    else:
        coords = all_data
    return coords

def get_coords_in_roi(all_coords,roi,file_type):
    """
        Returns the xy coordinates of the rectangular roi being processed
    """
    
    xstart = roi[0]
    xstop = roi[0]+roi[2]
    ystart = roi[1]
    ystop = roi[1]+roi[3]
    print 'roi:',roi
    print 'xstart,xstop,ystart,ystop:',xstart,xstop,ystart,ystop
    x = file_type['x_col']
    y = file_type['y_col']
    return all_coords[(all_coords[x] > xstart) & (all_coords[x] < xstop)
                      & (all_coords[y] > ystart) & (all_coords[y] < ystop)]
    
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
        if 'palmtracer' in file_type['name']:
            data.columns = ['track','orig_time','X position (pixel)','Y position (pixel)',\
                       'good','intensity','extra1','extra2','time'] 
    except:
        print 'there was a problem parsing localisation data'
        return None
    print 'reading the file took:',time.time()-s,'seconds'
    coords = get_all_locs(data,sizeC,file_type,pix_size)
    return coords     

def download_data(ann):
    """
        Downloads the specified file to and returns the path on the server
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
    file_path = os.path.join(PATH, ann.getFile().getName())
    shutil.rmtree(PATH)
         
def process_data(conn,image,file_type,sizeC,rectangles,locs,radius):
    """
        Calculates the density of localisations within a given radius
    """    
    x = file_type['x_col']
    y = file_type['y_col']
    locs_density = []
    
    for rect in rectangles:

        density = np.empty((1,4))
        
        for c in range(sizeC):
                            
            locs_df = get_coords_in_roi(locs[c],rect,file_type)
            chan = np.ones((len(locs_df.index),1))*c
            ld = localisation_density(locs_df.loc[:,[x,y]].values,radius)
            ld = np.concatenate((chan,locs_df.loc[:,[x,y]].values,ld),axis=1)
            density = np.vstack((density,ld))
            
        locs_density.append(density)
    
    return locs_density
                            
def run_processing(conn,script_params):
    file_anns = []
    message = ""
    imageIds = []
    image_id = script_params['ImageID']
    imageIds.append(image_id)
    image = conn.getObject("Image",image_id)
    if not image:
        message = 'Could not find specified image'
        return message
        
    file_id = script_params['AnnotationID']
    ann = conn.getObject("Annotation",file_id)
    if not ann:
        message = 'Could not find specified annotation'
        return message
    
    radius = script_params['Radius']
    
    #other parameters
    if script_params['Convert_coordinates_to_nm']:
        cam_pix_size = script_params['Parent_Image_Pixel_Size']
    else:
        cam_pix_size = 1
    file_type = FILE_TYPES[script_params['File_Type']]
     
    path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
    name,ext = os.path.splitext(path_to_ann)
    if ('txt' in ext) or ('csv' in ext):
        #get the path to the downloaded data
        path_to_data = download_data(ann)
        
        #get all the xy coords in that data
        locs = parse_sr_data(path_to_data,file_type,cam_pix_size)
        sizeC = len(locs)
        
        #get the rois to be processed
        rectangles = get_rectangles(conn,image_id)
        print 'rectanges:',rectangles
        
        #calculate local density
        locs_density = process_data(conn,image,file_type,sizeC,rectangles,locs,radius)
        
        #write the data to a csv
        file_name = "localisation_density_" + ann.getFile().getName()[:-4] + '.csv'
        with file(file_name, 'a') as outfile:
            outfile.write('# localisation density data for %s channels and %s ROIs: \n' % (sizeC, len(locs_density)))
            for r in range(len(locs_density)):
                outfile.write('# ROI %s\n' % rectangles[r][-1])
                outfile.write('Channel,%s,%s,Density within %s [nm]\n' % (file_type['x_col'],file_type['y_col'],str(radius)))
                density = locs_density[r][1:,:]
                np.savetxt(outfile, density, fmt='%-7.2f', delimiter=',', newline='\n')
                        
        new_file_ann, faMessage = script_util.createLinkFileAnnotation(
            conn, file_name, image, output="Wrote localisation density csv (Excel) file",
            mimetype="text/csv", desc=None)
        if new_file_ann:
            file_anns.append(new_file_ann)

        if not file_anns:
            faMessage = "No Analysis files created. See 'Info' or 'Error' for"\
                " more details"
        elif len(file_anns) > 1:
            faMessage = "Created %s csv (Excel) files" % len(file_anns)
    else:
        message = 'file annotation must be txt or csv'
        return message
    # clean up
    delete_downloaded_data(ann)
    
    message += faMessage
    return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Image')]
    
    fileTypes = [k for k in FILE_TYPES.iterkeys()]

    client = scripts.client('Localisation_Density_In_ROIs.py', """This script calculates the density of localisations 
within a distance scale set by the user (`Radius`).
Do not use `Convert coordinates to nm` option on Zeiss data.""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.Int("ImageID", optional=False, grouping="02",
        description="ID of super resolved image to process"),
        
    scripts.Int("AnnotationID", optional=False, grouping="03",
        description="ID of file to process"),
        
    scripts.String("File_Type", optional=False, grouping="04",
        description="Indicate the type of data being processed", values=fileTypes, default="zeiss2D"),
                            
    scripts.Int("Radius", optional=False, grouping="05",
        description="Distance scale for calculation (in nm)", default=50),

    scripts.Bool("Convert_coordinates_to_nm", optional=False, grouping="06",
        description="Convert to nm - DO NOT USE WITH ZEISS DATA", default=False),
                            
    scripts.Int("Parent_Image_Pixel_Size", grouping="06.1",
        description="Convert the localisation coordinates to nm (multiply by parent image pixel size)"),
        
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
