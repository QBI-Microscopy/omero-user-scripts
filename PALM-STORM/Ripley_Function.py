#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd
import os
import re
import time
from pair_correlation import ripleykfunction
import tempfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import shutil
from PIL import Image

import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
import omero.util.script_utils as script_utils
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
    try:
        os.remove(file_path)
    except OSError:
        pass
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
    
def upload_figure(conn, destination):
    """
    This creates a new Image in OMERO using all the images in destination folder as Z-planes
    """

    # Need to check whether we're dealing with RGB images (3 channels) or greyscale (1 channel)
    img = Image.open(destination)
    img.load()
    sizeC = len(img.split())
    sizeZ = 1
    sizeT = 1
    imageName = os.path.basename(destination)
    print 'imageName',imageName
    # Create a new Image in OMERO, with the jpeg images as a Z-stack.
    # We need a generator to produce numpy planes in the order Z, C, T.
    def plane_generator():
        img = Image.open(destination)
        img.load()      # need to get the data in hand before...
        channels = img.split()
        for channel in channels:
            numpyPlane = np.asarray(channel)
            yield numpyPlane

    # Create the image
    plane_gen = plane_generator()
    newImg = conn.createImageFromNumpySeq(plane_gen, imageName, sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT)
    print "New Image ID", newImg.getId()
    return newImg

def create_containers(conn,parent_image,child_image):
    
    updateService = conn.getUpdateService()
    parentDataset = parent_image.getParent()
    parentProject = parentDataset.getParent()
         
    if parentDataset is None:
        link = None
        print "No dataset created or found for new images."\
            " Images will be orphans."
    else:
        dsLink = omero.model.DatasetImageLinkI()
        dsLink.parent = omero.model.DatasetI(
            parentDataset.getId(), False)
        dsLink.child = omero.model.ImageI(child_image.getId(), False)
        updateService.saveObject(dsLink)
#     if parentProject and parentProject.canLink():
#         # and put it in the   current project
#         projectLink = omero.model.ProjectDatasetLinkI()
#         projectLink.parent = omero.model.ProjectI(
#             parentProject.getId(), False)
#         projectLink.child = omero.model.DatasetI(
#             parentDataset.getId(), False)
#         updateService.saveAndReturnObject(projectLink) 
        
def attach_results(conn,ann,image,data,sizeC,sizeR):
    
    file_name = "ripleyl_plot_" + ann.getFile().getName()[:-4] + '.csv' 
    with file(file_name, 'w') as outfile:
        outfile.write('# ripley data for %s channels and %s ROIs: \n' % (sizeC, sizeR ))
        data.to_csv(outfile,sep=',',float_format='%8.2f',index=False,encoding='utf-8')
    
    description = "Ripley L function data created from:\n  Image ID: %d Annotation_ID: %d"\
                    % (image.getId(),ann.getId())        
    new_file_ann, faMessage = script_util.createLinkFileAnnotation(
        conn, file_name, image, output="Ripley L Plot csv (Excel) file",
        mimetype="text/csv", desc=description)     
    return new_file_ann
        
    
def process_data(conn,image,file_type,sizeC,rectangles,coords,rmax):
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

    x = FILE_TYPES[file_type]['x_col']
    y = FILE_TYPES[file_type]['y_col']
    f = FILE_TYPES[file_type]['frame']
    dist_scale = np.linspace(0,rmax,200)
    ripley_list = []
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
        stops = [coords[0][f].max]
    
    if sizeT > 10:
        # plot every 5th time point
        interval = 5
    else:
        interval = sizeT
        
    plt.figure() 
    for c in range(sizeC):
        
        chan = np.ones(dist_scale.shape[0])*c
        r_df = pd.DataFrame(chan,columns=['channel'])
        r_df['radius'] = dist_scale
        locs_df = coords[c]
        legend = []
        for t in range(sizeT):
            conn.keepAlive()
            coords_in_frames = locs_df[(locs_df[f]>= starts[t]) & (locs_df[f]<= stops[t])]
            for rect in rectangles:
                rid = rect[-1]
                locs = get_coords_in_roi(coords_in_frames,rect,file_type) 
                if len(locs.index) > 0:      
                    box = [rect[0],rect[0]+rect[2],rect[1],rect[1]+rect[3]]
                    s = time.time()
                    l = ripleykfunction(locs.loc[:,[x,y]].values,dist_scale,box,0)
                    print 'Ripley calculation took:', time.time() - s
                    ripley_column = 'Ripley L ROI_%s_time0%s' % (rid,t)
                    r_df[ripley_column] = l
                    if t % interval == 0:
                        s = time.time()
                        plt.plot(r_df.loc[:,['radius']].values,r_df.loc[:,[ripley_column]])
                        legend.append('ROI_%s_timepoint_%s'%(rid,t))
                        print 'Ripley plot took:',time.time() - s
            ripley_list.append(r_df)
        
    ripley_df = pd.concat(ripley_list).reset_index(drop=True)
    ripfig_path = os.path.join(PATH,'RipleyPlots')
    plt.legend(legend)
    plt.xlabel('Radius (nm)')
    plt.ylabel('L - r')
    plt.savefig(ripfig_path)
    ripley_fig = upload_figure(conn,ripfig_path+'.png')
    return ripley_df,ripley_fig
                            
def run_processing(conn,script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """    
    dataType = script_params["Data_Type"]
    updateService = conn.getUpdateService()
    
    file_anns = []
    message = ""
    
    if script_params['Convert_coordinates_to_nm']:
        cam_pix_size = script_params['Parent_Image_Pixel_Size']
    else:
        cam_pix_size = 1
    file_type = script_params['File_Type']
    rmax = script_params['Max_radius']
    
    file_ids = script_params['AnnotationIDs']

    # Get the images
    objects, logMessage = script_utils.getObjects(conn, script_params)
    message += logMessage
    if not objects:
        return None, message

    # Concatenate images from datasets
    if dataType == 'Image':
        images = objects
    else:
        images = []
        for ds in objects:
            images += ds.listChildren()

    # Check for rectangular ROIs and filter images list
    images = [image for image in images if image.getROICount("Rect") > 0]
    if not images:
        message += "No rectangle ROI found."
        return None, message

    total_rois = sum([i.getROICount("Rect") for i in images])
    if total_rois > 10:
        message += "Cannot start batch processing - too many rois (maximum is 10)."
        return None, message
        
    image_ids = [i.getId() for i in images]

    ripley = []
    figures = []
    figure_ids = []
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
            
            #calculate the ripley function in each roi
            ripley_data,ripley_figure = process_data(conn,image,file_type,sizeC,rectangles,coords,rmax)
            ripley.append(ripley_data)
            figures.append(ripley_figure)
            figure_ids.append(ripley_figure.getId())
            
            create_containers(conn,image,ripley_figure)
                
            new_file_ann = attach_results(conn,ann,ripley_figure,ripley_data,sizeC,rectangles)
            
            if new_file_ann:
                file_anns.append(new_file_ann)
                
            if not file_anns:
                faMessage = "No Analysis files created. See 'Info' or 'Error' for"\
                    " more details"
            elif len(file_anns) > 1:
                faMessage = "Created %s csv (Excel) files" % len(file_anns)
            elif len(file_anns) == 1:
                faMessage = "Created a new csv (Excel) file and attached to image ID %s" \
                % ripley_figure.getId()
                
            # clean up
            delete_downloaded_data(ann)
            
            message += faMessage
        else:
            message = 'file annotation must be txt or csv'
            return message
        
    robj = (len(figures) > 0) and figures[0]._obj or None
    return robj, message    

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """
    printDuration(False)

    dataTypes = [rstring('Image')]
    
    fileTypes = [k for k in FILE_TYPES.iterkeys()]

    client = scripts.client('Ripley_Lfunction.py', """This script calculates the Ripley L function for OMERO ROIs on a 
reconstructed super resolution image.
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
        "Email_Results", grouping="07", default=False,
        description="E-mail the results"),
                        
    scripts.String("Email_address", grouping="07.1",
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
        robj,message = run_processing(conn, scriptParams)
        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))        
        #client.setOutput("Message", rstring("No plates created. See 'Error' or 'Info' for details"))
    finally:
        client.closeSession()
        printDuration()
        
if __name__ == "__main__":
    run_as_script()
