#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import time
import numpy as np
import csv
import tempfile
from scipy.spatial.distance import cdist

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *

def del_alignment(dir_path):
    for old_file in os.listdir(dir_path):
        file_path = os.path.join(dir_path, old_file)
        os.unlink(file_path)
               
def pad(coords):
    return np.hstack([coords, np.ones((coords.shape[0], 1))])
    
def unpad(coords):
    return coords[:,:-1]
    
def calculate_alignment(unreg,base):
    A, reg, rank, s = np.linalg.lstsq(pad(unreg), pad(base))
    A[np.abs(A) < 1e-10] = 0  # set really small values to zero
    return [base,reg,A]
    
def do_transform(coords,A):
    return unpad(np.dot(pad(coords), A))

def create_distance_matrix(pointsA,pointsB):   
    return np.sort(cdist(np.array(pointsA),np.array(pointsB),'euclidean'))

def nearest_neighbour(dataXY,col=1):
    dist_mat = create_distance_matrix(dataXY,dataXY)
    nnDist = dist_mat[:,col]
    return nnDist

def dist_search(A,B,dist_thresh=5):
    dist_mat = create_distance_matrix(A.T,B.T)  
    nn_dist = nearest_neighbour(dist_mat,0)
    idx = nn_dist < dist_thresh
    return idx

def find_linked(self,listA,listB):
    linked = dist_search(np.array(listA)[:,0:2],np.array(listB)[:,0:2])
    return [v for i,v in enumerate(listA) if linked[i]]

def fit_psf():
    pass

def detect_beads(image):
    t = image.getSizeT()
    z = image.getSizeZ()
    if (t > 1) and (z > 1):
        return 'data to be transformed should have multiple z or t but not both'

def getPoints(conn, imageId):

    rois = []

    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)

    for roi in result.rois:
        cx = None
        cy = None
        
        for shape in roi.copyShapes():
            if type(shape) == omero.model.PointI:
                if (cx is None) and (cy is None):
                    cx = int(shape.getCx().getValue())
                    cy = int(shape.getCy().getValue())
                    t = int(shape.getTheT().getValue())
                    z = int(shape.getTheZ().getValue())
                    
            rois.append((cx,cy,t,z))
            
    return rois

def run_processing(conn, script_params):
    
    message = ""
    input_dir = tempfile.mkdtemp(prefix='chanalign')
    
    if len(script_params['IDs']) != 2:
        message += 'not enough image IDs provided'
        return message
    
    base = conn.getObject('Image',script_params['IDs'][0])  
    if not base:
        message += 'could not find reference image'
        return message
    
    unreg = conn.getObject('Image',script_params['IDs'][1])
    if not unreg:
        message += 'could not find image to be transformed'
        return message
       
    base_points = getPoints(conn, script_params['IDs'][0])
    if base_points:
    # then we are working with manually defined rois
        base_points = fit_psf(base,base_points)
    else:
    # then we are auto detecting
        base_points = detect_beads(base)
    
    unreg_points = getPoints(conn, script_params['IDs'][1])
    if unreg_points:
    # then we are working with manually defined rois
        unreg_points = fit_psf(unreg,unreg_points)
    else:
    # then we are auto detecting
        unreg_points = detect_beads(unreg)
        
    if (len(base_points) > 0) and (len(unreg_points) > 0): 
        base_points = find_linked(base_points,unreg_points)
        unreg_points = find_linked(unreg_points,base_points)  
        
    transform = calculate_alignment(base_points,unreg_points)
    
    fpath = os.path.join(input_dir,'channel_alignment.csv')        
    with open(fpath,'wb') as f:
        writer = csv.writer(f)
        writer.writerows(transform)  
        
    description = "Channel alignment created for:\n  Base image ID: %d\n Unregistered image ID: %d"\
                    % (base.getId(),unreg.getId())        
    new_file_ann, message = script_util.createLinkFileAnnotation(
        conn, fpath, base, output="Channel alignment csv (Excel) file",
        mimetype="text/csv", desc=description)
    file_anns = []
    if new_file_ann:
        file_anns.append(new_file_ann)

    if not file_anns:
        message = "No Analysis files created. See 'Info' or 'Error' for"\
            " more details"
    elif len(file_anns) > 0:
        message = "Created %s csv (Excel) files" % len(file_anns)    
        
    del_alignment(fpath)    
          

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service,
    passing the required parameters.
    """

    dataTypes = [rstring('Image')]

    client = scripts.client('Align_PSF_Beads.py', """This script will create an affine alignment file that
can be used to align two channel PALM/STORM data. Users should provide the script with two datasets, one
for each channel. If any point ROIs are present in the datasets, the script will use these for alignment,
otherwise automatic object detection will be performed.""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of datasets to be aligned."),                           
        
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