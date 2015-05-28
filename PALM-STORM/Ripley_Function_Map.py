#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
from scipy.interpolate import Rbf
from scipy.spatial.distance import cdist
import os
import shutil
import re
import time
import math
from pair_correlation import ripleykperpoint
import tempfile

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

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
ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'
startTime = 0

def printDuration(output=True):
    global startTime
    if startTime == 0:
        startTime = time.time()
    if output:
        print "Script timer = %s secs" % (time.time() - startTime)


def list_image_names(conn, ids, file_anns):
    """
    Builds a list of the image names
    
    @param conn: The BlitzGateway connection
    @param ids: Python list of image ids
    """
    image_names = []
    for i,image_id in enumerate(ids):
        img = conn.getObject('Image', image_id)
        if not img:
            continue

        ds = img.getParent()
        if ds:
            pr = ds.getParent()
        else:
            pr = None

        image_names.append("[%s][%s] Image %d : %s : %s" % (
                           pr and pr.getName() or '-',
                           ds and ds.getName() or '-',
                           image_id, os.path.basename(img.getName()),
                           file_anns[i].getFile().getName()))

    return image_names

def validate_email(conn, params):
    """
    Checks that a valid email address is present for the user_id

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    """
    userEmail = ''
    if params['Email_address']:
        userEmail = params['Email_address']
    else:
        user = conn.getUser()
        user.getName() # Initialises the proxy object for simpleMarshal
        dic = user.simpleMarshal()
        if 'email' in dic and dic['email']:
            userEmail = dic['email']

    params['Email_address'] = userEmail
    print userEmail
    # Validate with a regular expression. Not perfect but it will do
    return re.match("^[a-zA-Z0-9._%-]+@[a-zA-Z0-9._%-]+.[a-zA-Z]{2,6}$",
                    userEmail)
    
def email_results(conn,params,image_ids,file_anns):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """

    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids, file_anns)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = '[OMERO Job] Ripley LFunction'
    msg.attach(MIMEText("""
New Ripley L function calculation results files created:

Format:
[parent project/datset] image id : image name : result filename

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()

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
        roi_id = roi.getId().getValue()
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
            rois.append((x*physX, y*physY, width*physX, height*physY, zStart, zEnd, tStart, tEnd, roi_id))

    return rois

def get_all_locs_in_chan(all_data,chan=0,chancol=None):
    """
    Extracts localisation coordinates for the specified channel
    
    @param all_data:    the pandas dataframe containing all parsed data
    @param chan:        the channel being extracted
    @param chancol:     the column index which specfies the channel
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
    
    @param path:         the path of the localisations file being parsed
    @param file_type:    the type of dataset we are working on
                         (see FILE_TYPES dictionary)
    @param pix_size:     used to convert localisation coordinates to nm
                         (except for Zeiss files)
    """
    working_file_type = FILE_TYPES[file_type]
    header_row = working_file_type['header_row']

    if 'zeiss2chan2D' in file_type:
        sizeC = 2
        chancol = FILE_TYPES[file_type]['chan_col']
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
        if 'palmtracer' in working_file_type['name']:
            data.columns = ['track','orig_time','X position (pixel)','Y position (pixel)',\
                       'good','intensity','extra1','extra2','time']         
    except:
        print 'there was a problem parsing localisation data'
        return None
    print 'reading the file took:',time.time()-s,'seconds'
    coords = get_all_locs(data,sizeC,working_file_type,pix_size)
    return coords      

def download_data(ann):
    """
    Downloads the specified file to and returns the path on the server
    
    @param ann:    the file annotation being downloaded
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
    Deletes the downloaded file annoation
    
    @param ann:    the file annotation
    """
    file_path = os.path.join(PATH, ann.getFile().getName())
    shutil.rmtree(PATH)
    
def get_coords_in_roi(all_coords,roi,file_type):
    """
    Returns the xy coordinates of the rectangular roi being processed
    
    @param all_coords:    the pandas dataframe holding all parsed data
    @param roi:           the region of interest we are working on
    @param file_type:    the type of dataset we are working on
                         (see FILE_TYPES dictionary)    
    """
    xstart = roi[0]
    xstop = roi[0]+roi[2]
    ystart = roi[1]
    ystop = roi[1]+roi[3]
    print 'roi:',roi
    print 'xstart,xstop,ystart,ystop:',xstart,xstop,ystart,ystop
    x = FILE_TYPES[file_type]['x_col']
    y = FILE_TYPES[file_type]['y_col']
    return all_coords[(all_coords[x] > xstart) & (all_coords[x] < xstop)
                      & (all_coords[y] > ystart) & (all_coords[y] < ystop)]
    
def process_data(conn,script_params,image,file_type,sizeC,rectangles,coords,rmax):
    """
    Calculates the ripley l function for coordinates in user-defined 
    rectangular region of interest
    
    @param conn:          the BlitzGateWay connection
    @param image:         the image being processed
    @param sizeC:         number of channels in the image
    @param rectangles:    the regions of interest
    @param coords:        the localisation coordinates
    @param rmax:          maximum distance scale for Ripley calculation
    
    """    
    updateService = conn.getUpdateService()
    parentDataset = image.getParent()
    parentProject = parentDataset.getParent()
    x = FILE_TYPES[file_type]['x_col']
    y = FILE_TYPES[file_type]['y_col']
    frame = FILE_TYPES[file_type]['frame']
    sizeZ = 1
    sizeT = image.getSizeT()
    if sizeT > 1:
        desc = image.getDescription()
        
        if desc:
            start = desc.index('Start')
            stop = desc.index('Stop')
            starts = desc[start+7:stop-3]
            starts = [int(s) for s in starts.split(',')] 
            stops = desc[stop+6:len(desc)-1]
            stops = [int(s) for s in stops.split(',')]
    else:
        starts = [1]
        stops = [coords[0][frame].max]
    
    new_images = []  
    new_ids = []
    for r,rect in enumerate(rectangles):
        planes = []
        ldf = []
        for c in range(sizeC):
            locs_df = coords[c]
            tt = 0
            for t in range(sizeT):
                conn.keepAlive()
                coords_in_frames = locs_df[(locs_df[frame]>= starts[t]) & (locs_df[frame]<= stops[t])]
                locs = get_coords_in_roi(coords_in_frames,rect,file_type)       
                box = [rect[0],rect[0]+rect[2],rect[1],rect[1]+rect[3]]
                pixelsX = math.ceil(float(rect[2] / 50.0))
                pixelsY = math.ceil(float(rect[3] / 50.0))
                print 'pixelsX,pixelsY:',pixelsX,pixelsY
                
                if len(locs.index)>0:
                    l = ripleykperpoint(locs.loc[:,[x,y]].values,rmax,box,0)
                    tx = np.linspace(box[0], box[1], pixelsX)
                    ty = np.linspace(box[2], box[3], pixelsY)
                    XI, YI = np.meshgrid(tx, ty)
                    print 'num x vals,num y vals:',locs.loc[:,[x]].values.shape,locs.loc[:,[y]].values.shape
                    rbf = Rbf(locs.loc[:,[x]].values, locs.loc[:,[y]].values, l, epsilon=2)
                    ZI = rbf(XI, YI)
                    ZI[ZI<0.0] = 0.0
                else:
                    ZI = np.zeros((pixelsY,pixelsX))
                    l = np.zeros(100)
                print 'ZI shape:',ZI.shape
                planes.append(ZI)
                ldf.append(pd.DataFrame(l))
        
        ripley_df = pd.concat(ldf,join='outer',axis=1)
                        
        def plane_gen():
            for p in planes:
                yield p  
                        
        imageName = 'Clusters_ROI_ID%s.ome.tif'%rect[-1]                
        description = "Cluster map for ROI%s of ImageID%s" % (rect[-1],image.getId())
        newImg = conn.createImageFromNumpySeq(
            plane_gen(), imageName,
            sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT,
            description=description)        
        
        new_images.append(newImg)
        new_ids.append(newImg.getId())
         
        file_name = "RLPP_ROI%s.csv" % (r)
        with open(file_name,'w') as f:
            f.write('# ripley per point data for %s channels and %s timepoints for ROI%s: \n' % (sizeC, sizeT, rect[-1]))
            ripley_df.to_csv(f,sep=',',float_format='%8.2f',index=False,encoding='utf-8')
    
        new_file_ann, faMessage = script_util.createLinkFileAnnotation(
            conn, file_name, newImg, output="wrote ripley per point data",
            mimetype="text/csv", desc=None)

    if script_params['New_Dataset'] and \
       len(script_params['Container_Name'].strip()) > 0:
        # create a new dataset for new images
        datasetName = script_params['Container_Name']
        print "\nMaking Dataset '%s' of Images from ROIs of Image: %s" \
            % (datasetName, image.getId())
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        desc = "Images in this Dataset are from ROIs of parent Image:\n"\
            "  Name: %s\n  Image ID: %d" % (image.getName(), image.getId())
        dataset.description = rstring(desc)
        dataset = updateService.saveAndReturnObject(dataset)
        parentDataset = dataset
    else:
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
        link = []
        for cid in new_ids:
            dsLink = omero.model.DatasetImageLinkI()
            dsLink.parent = omero.model.DatasetI(
                parentDataset.id.val, False)
            dsLink.child = omero.model.ImageI(cid, False)
            updateService.saveObject(dsLink)
            link.append(dsLink)
        if parentProject and parentProject.canLink():
            # and put it in the   current project
            projectLink = omero.model.ProjectDatasetLinkI()
            projectLink.parent = omero.model.ProjectI(
                parentProject.getId(), False)
            projectLink.child = omero.model.DatasetI(
                dataset.id.val, False)
            updateService.saveAndReturnObject(projectLink)
    return new_images, dataset, link, new_ids
                            
def run_processing(conn,script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """    
    file_anns = []
    message = ""

    if script_params['Convert_coordinates_to_nm']:
        cam_pix_size = script_params['Parent_Image_Pixel_Size']
    else:
        cam_pix_size = 1
    file_type = script_params['File_Type']
    rmax = script_params['Max_radius']
    
    file_ids = script_params['AnnotationIDs']
    image_ids = script_params['IDs']
    new_images = []
    new_datasets = []
    links = []
    for i,id in enumerate(image_ids):
        image = conn.getObject("Image",id)

        if not image:
            message = 'Could not find specified image'
            return message
            
        ann = conn.getObject("Annotation",file_ids[i])
        if not ann:
            message = 'Could not find specified annotation'
            return message
                
        path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
        name,ext = os.path.splitext(path_to_ann)
        if ('txt' in ext) or ('csv' in ext):
            #download the localisations data file
            path_to_data = download_data(ann)
            
            #get all xy coords from the file
            coords = parse_sr_data(path_to_data,file_type,cam_pix_size)
            
            #determine the number of channels
            sizeC = len(coords)
            
            #get the regions of interest
            rectangles = get_rectangles(conn,id)
            if rectangles:
                #calculate the ripley function in each roi
                new_image, new_dataset, link, new_id = process_data(conn,script_params,image,file_type,sizeC,rectangles,coords,rmax)
                if new_image is not None:
                    if isinstance(new_image, list):
                        new_images.extend(new_image)
                    else:
                        new_images.append(new_image)
                if new_dataset is not None:
                    new_datasets.append(new_dataset)
                if link is not None:
                    if isinstance(link, list):
                        links.extend(link)
                    else:
                        links.append(link)
        else:
            message = 'file annotation must be txt or csv'
            return message
        # clean up
        delete_downloaded_data(ann)
        
    if new_images:
        if len(new_images) > 1:
            message += "Created %s new images" % len(new_images)
        else:
            message += "Created a new image"
    else:
        message += "No image created"

    if new_datasets:
        if len(new_datasets) > 1:
            message += " and %s new datasets" % len(new_datasets)
        else:
            message += " and a new dataset"

    if script_params['Email_Results'] and file_anns:
        email_results(conn,script_params,image_ids,file_anns)
    
    return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """
    printDuration(False)
    
    dataTypes = [rstring('Image')]
    
    fileTypes = [k for k in FILE_TYPES.iterkeys()]

    client = scripts.client('Ripley_Lfunction.py', """This script calculates the Ripley L function for OMERO ROIs on a 
reconstructed super resolution image at a specific distance scale set by the user (`Max radius`).
Do not use `Convert coordinates to nm` option on Zeiss data""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="ID of super resolved image to process"),
        
    scripts.List("AnnotationIDs", optional=False, grouping="03",
        description="ID of file to process"),
        
    scripts.String("File_Type", optional=False, grouping="04",
        description="Indicate the type of data being processed", values=fileTypes, default="zeiss2D"),

    scripts.Bool("Convert_coordinates_to_nm", optional=False, grouping="05",
        description="Convert localisation coordinates to nm - DO NOT USE WITH ZEISS DATA", default=False),
                            
    scripts.Int("Parent_Image_Pixel_Size", grouping="05.1",
        description="Convert the localisation coordinates to nm (multiply by parent image pixel size)"),
        
    scripts.Int("Max_radius", optional=False, grouping="06",
        description="Maximum distance scale for calculation (in nm)", default=1000),
                            
    scripts.Bool(
        "New_Dataset", grouping="07", default=False,
        description="Make a new dataset for the ROIs"),
                                                        
    scripts.String("Container_Name", grouping="07.1",
        description="Option: put Images in new Dataset with this name",
        default="From_ROIs"),
        
    scripts.Bool("Email_Results", grouping="08", default=False,
        description="E-mail the results"),
                        
    scripts.String("Email_address", grouping="08.1",
    description="Specify e-mail address"), 
        
    authors = ["Daniel Matthews", "QBI"],
    institutions = ["University of Queensland"],
    contact = "d.matthews1@uq.edu.au",
    )

    try:
        # this could run for a long time so keepAlive
        client.enableKeepAlive(3600)
        
        # process the list of args above.
        scriptParams = {}
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)

        print scriptParams

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)
        
        # validate email address if provided
        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return        

        # process images in Datasets
        message = run_processing(conn, scriptParams)
        client.setOutput("Message", rstring(message))
        
    finally:
        client.closeSession()
        printDuration()
        
if __name__ == "__main__":
    run_as_script()
