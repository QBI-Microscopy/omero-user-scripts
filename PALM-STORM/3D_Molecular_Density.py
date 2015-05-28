#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import time
import numpy
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.model import LengthI
from omero.model.enums import UnitsLength
from random import random
import math
from numpy import array,histogramdd
import pandas as pd
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
import tempfile
import glob
import itertools

FILE_TYPES = {
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

def calc_hist(data,file_type,nr,nc,np,size_r,size_c,size_p):
    """
    Does the histogram calculation using numpy.histogramdd
    
    @param data: the coordinates to bin
    @param file_type:    the type of dataset we are dealing with (see FILE_TYPES global)    
    @param size_r: the rows in the histogram represent a real-space distance 0 --> size_r
    @param size_c: the cols in the histogram represent a real-space distance 0 --> size_c
    @param size_p: the pages in the histogram represent a real-space distance 0 --> size_p
    @param nr:     the number of bins in the y direction
    @param nc:     the number of bins in the x direction
    @param np:     the number of bins in the z direction
    """    

    x = file_type['x_col']
    y = file_type['y_col']
    z = file_type['z_col']
    return histogramdd(data.loc[:,[y,x,z]].values,range=((0,size_r),(0,size_c),(0,size_p)),bins=(nr,nc,np))[0]
    
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
        
def get_all_locs(all_data,sizeC,chancol):
    """
    Returns the xy coordinates from the input data in a numpy array
    
    @param all_data: all the data read from the localisations file
    @param sizeC:    how many channels in the dataset
    @param chancol:  which column in all_data where we will find the channel assignment
    """    
    coords = [] 
    print 'sizeC:',sizeC
    for c in range(sizeC):
        coords.append(get_all_locs_in_chan(all_data,c,chancol))   
    return coords
                    
def parse_sr_data(path,file_type):
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
    coords = get_all_locs(data,sizeC,chancol)
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
    
def process_data(conn,image,file_type,file_id,locs,sr_pix_size,srz_pix_size,z_range,nm_per_pixel):
    """
    Run the processing and upload the resultant image
    
    @param conn:         Blitzgateway connection
    @param image:        we need to get the image where the localisation data is attached --> this should be the raw timelapse data
    @param file_type:    the type of dataset we are dealing with (see FILE_TYPES global)
    @param file_id:      the annotation id of the localisations file
    @param coords:       the coords being histogrammed
    @param sr_pix_size:  the pixel size we want in the new image in nm
    @param z_range:      z range covered in one z-plane (4um for Zeiss 3D)
    @param nm_per_pixel: the size of the pixel in the original image in nm (1 for Zeiss data which is already in nm)
    """
        
    imageName = image.getName()
    name,ext = os.path.splitext(imageName)
    if 'ome' in name:
        name = name.split('.')[0]
        new_name = name + '_sr_histogram.ome' + ext
        sizeZ = image.getSizeT()         
    else:
        new_name = name + '_sr_histogram' + ext
        sizeZ = image.getSizeZ()  
    parentDataset = image.getParent()
    parentProject = parentDataset.getParent()
    updateService = conn.getUpdateService()
    
    frame_width = image.getSizeX()
    print 'frame_width',frame_width
    frame_height = image.getSizeY()
    print 'frame_height',frame_height
         

    stepZ = nm_per_pixel[2]
    print 'sizeZ,stepZ',sizeZ,stepZ
    if 'zeiss2chan2D' in file_type:
        sizeC = 2
    else:
        sizeC = 1
    
    sizeT = 1    
    rangex = frame_width * nm_per_pixel[0]
    binsx = rangex / sr_pix_size
    rangey = frame_height * nm_per_pixel[1]
    binsy = rangey / sr_pix_size
    rangez = float(z_range) + (sizeZ-1)*stepZ
    print 'rangez',rangez
    binsz = int(math.ceil(rangez / srz_pix_size))
    print 'sizeC,binsy,binsx,binsz',sizeC,binsy,binsx,binsz
    hist_data = numpy.zeros((sizeC,binsy,binsx,binsz))
    for c in range(sizeC):
        hist = calc_hist(locs[c],file_type,binsy,binsx,binsz,rangey,rangex,rangez)
        hist_data[c,:,:,:] = hist
        
    def plane_gen():
        for z in range(binsz):
            for c in range(sizeC):
                for t in range(sizeT):
                    plane = hist_data[c,:,:,z]
                    yield plane     
                    
    description = "Created from image:\n  Name: %s\n  File ID: %d" % (imageName, file_id)
    newImg = conn.createImageFromNumpySeq(
        plane_gen(), new_name,
        sizeZ=binsz, sizeC=sizeC, sizeT=sizeT,
        description=description)
    
    # reload the image (also reloads pixels)
    image = conn.getObject("Image", newImg.getId())
    
    # Get the BlitzGateway wrapped pixels and unwrap it
    pixelsWrapper = image.getPrimaryPixels()
    pixels = pixelsWrapper._obj
    
    # Update and save
    pixSize = LengthI(float(sr_pix_size) / 1000, UnitsLength.MICROMETER)
    pixSizeZ = LengthI(float(srz_pix_size) / 1000, UnitsLength.MICROMETER)
    pixels.setPhysicalSizeX( pixSize )
    pixels.setPhysicalSizeY( pixSize )
    pixels.setPhysicalSizeZ( pixSize )
    updateService.saveObject(pixels)    

    if newImg:
        iid = newImg.getId()
        print "New Image Id = %s" % iid
        # put new images in existing dataset
        dataset = None
        if parentDataset is not None and parentDataset.canLink():
            parentDataset = parentDataset._obj
        else:
            parentDataset = None
        parentProject = None    # don't add Dataset to parent.
    
        if parentDataset is None:
            link = None
            print "No dataset created or found for new images."\
                " Images will be orphans."
        else:
            dsLink = omero.model.DatasetImageLinkI()
            dsLink.parent = omero.model.DatasetI(
                parentDataset.id.val, False)
            dsLink.child = omero.model.ImageI(iid, False)
            updateService.saveObject(dsLink)
            if parentProject and parentProject.canLink():
                # and put it in the   current project
                projectLink = omero.model.ProjectDatasetLinkI()
                projectLink.parent = omero.model.ProjectI(
                    parentProject.getId(), False)
                projectLink.child = omero.model.DatasetI(
                    dataset.id.val, False)
                updateService.saveAndReturnObject(projectLink)   
        message = 'Super resolution histogram successfully created'
    else:
        message = 'Something went wrong, could not make super resolution histogram'
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
    sr_pix_size = script_params['SR_XY_pixel_size']
    srz_pix_size = script_params['SR_Z_pixel_size']

    file_type = FILE_TYPES[script_params['File_Type']]

    pixels = image.getPrimaryPixels()
    physicalSizeX = math.ceil(pixels.physicalSizeX.getValue()*1000.0)
    physicalSizeY = math.ceil(pixels.physicalSizeY.getValue()*1000.0)
    if pixels.getPhysicalSizeZ():
        physicalSizeZ = math.ceil(pixels.physicalSizeZ.getValue()*1000.0)
        z_range = 4000.0 # hardwired z range for zeiss 3d data
    else:
        physicalSizeZ = 1000.0
        z_range = 4000.0
        
    if (physicalSizeX is None) or (physicalSizeY is None):
        message = 'physical pixel size required'
        return message
    
    if physicalSizeX != physicalSizeY:
        message = 'pixel size for x and y should not be different'
        return        
        
    nm_per_pixel = [physicalSizeX,physicalSizeY,physicalSizeZ]
    print 'nm_per_pixel',nm_per_pixel
    path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
    name,ext = os.path.splitext(path_to_ann)
    if ('txt' in ext) or ('csv' in ext):
        path_to_data = download_data(ann)
        coords = parse_sr_data(path_to_data,file_type)
        faMessage = process_data(conn,image,file_type,file_id,coords,sr_pix_size,srz_pix_size,z_range,nm_per_pixel)

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

    client = scripts.client('Build_3D_Histogram_From_Localisations.py', """This script creates a 3D histogram from PALM/STORM data. 
Only Zeiss 3D data is supported (4um Z range).""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.Int("ImageID", optional=False, grouping="02",
        description="ID of parent localisation microscopy movie"),
        
    scripts.Int("AnnotationID", optional=False, grouping="03",
        description="ID of file to process"),
        
    scripts.String("File_Type", optional=False, grouping="04",
        description="Indicate the type of data being processed", values=fileTypes, default="zeiss3D"),
        
    scripts.Int("SR_XY_pixel_size", optional=False, grouping="05",
        description="Pixel size in super resolved image in nm"),

    scripts.Int("SR_Z_pixel_size", optional=False, grouping="06",
        description="Pixel size in super resolved image in nm"),
                            
#     scripts.Int("Z_range", optional=False, grouping="07w",
#         description="Z range captured in single plane in micrometres (4um for Zeiss 3D)",default=4),
                            
#     scripts.Bool("Set_Parent_Pixel_Size", optional=True, grouping="07.1",
#         description="Use if physical pixel size is not set in parent image", default=False),
#                                                         
#     scripts.Int("Parent_Image_XY_Pixel_Size", optional=True, grouping="07.2",
#         description="Convert the localisation coordinates to nm (multiply by parent image pixel size)"),
# 
#     scripts.Int("Parent_Image_Z_Step", optional=True, grouping="07.3",
#         description="If multiple z planes were recorded, what was the step size"),
                                    
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
