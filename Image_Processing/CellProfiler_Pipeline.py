#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function # import of python3 print --> use with brackets
import omero
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject
import omero.scripts as scripts

import os
import csv
import re
import sys
import glob
import subprocess
import shutil
import tempfile
import itertools
from collections import defaultdict
from tifffile import imread,imsave,TiffFile
from PIL import Image
from libtiff import TIFF
import numpy as np
from numpy import zeros, int32, asarray
from cStringIO import StringIO

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'
input_dir = ''
output_dir = ''

def print_obj(obj, indent=0):
    """
    Helper method to display info about OMERO objects.
    Not all objects will have a "name" or owner field.
    """
    print( """%s%s:%s  Name:"%s" (owner=%s)""" % (
        " " * indent,
        obj.OMERO_CLASS,
        obj.getId(),
        obj.getName(),
        obj.getOwnerOmeName()))

def get_rects_from_rois(conn, imageId):
    """ 
    Returns a list of (x, y, width, height), one for each ROI with rectangle shape

    @param conn:        BlitzGateway connection
    @param imageId:     Image ID
    """

    # Using the underlying ROI service & omero.model objects (no ROI support in Blitz Gateway yet)
    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)

    rects = []
    for roi in result.rois:
        # go through all the shapes of the ROI
        roi_id = roi.getId().getValue()
        for shape in roi.copyShapes():
            if shape.__class__.__name__ == 'RectI':
                x = shape.getX().getValue()     # Need getValue() for omero.model rtypes
                y = shape.getY().getValue()
                w = shape.getWidth().getValue()
                h = shape.getHeight().getValue()
                rects.append( (x,y,w,h,roi_id) )
                break    # Only use the first Rect we find per ROI
    return rects

def download_raw_planes_as_tiles(conn,source,channels,channel_names,box):
    """
    Creates two generators - one to get tiles from the parent image and
    one to upload those tiles to the new child image
    
    @param conn: The BlitzGateway connection
    @param source: The parent image and source of tiles
    @param image_name: The name of the new child image
    @param description: A description of the new child image
    @param box: The coordinates of the ROI on the parent image being processed
    """        

    if box:
        xbox, ybox, wbox, hbox, z1box, z2box, t1box, t2box = box
        sizeX = wbox
        sizeY = hbox
    else:
        sizeX = source.getSizeX()
        sizeY = source.getSizeY()
        
    sizeZ = source.getSizeZ()
    sizeT = source.getSizeT()
    sizeC = source.getSizeC()
    name,ext = os.path.splitext(source.getName())
    tileWidth = 1024
    tileHeight = 1024
    primary_pixels = source.getPrimaryPixels()

    # Make a list of all the tiles we're going to need.
    # This is the SAME ORDER that RPSTileLoop will ask for them.
    zctTileList = []
    for z in range(0, sizeZ):
        for c in range(0, sizeC):
            for t in range(0, sizeT):
                for tileOffsetY in range(
                        0, ((sizeY + tileHeight - 1) / tileHeight)):
                    for tileOffsetX in range(
                            0, ((sizeX + tileWidth - 1) / tileWidth)):
                        x = tileOffsetX * tileWidth
                        y = tileOffsetY * tileHeight
                        w = tileWidth
                        if (w + x > sizeX):
                            w = sizeX - x
                        h = tileHeight
                        if (h + y > sizeY):
                            h = sizeY - y
                        tile_xywh = (box[0] + x, box[1] + y, w, h)
                        zctTileList.append((z, c, t, tile_xywh))

    # This is a generator that will return tiles in the sequence above
    # getTiles() only opens 1 rawPixelsStore for all the tiles
    # whereas getTile() opens and closes a rawPixelsStore for each tile.
    tile_gen = primary_pixels.getTiles(zctTileList)

    def next_tile():
        return tile_gen.next()
    
    image_names = {}
    for chan in channel_names:
        image_names[chan] = []
    
    tile_count = 0
    for z in range(sizeZ):
        for i,c in enumerate(channels):
            cName = channel_names[i]
            for t in range(sizeT):
                tif_image = TIFF.open('%s_z%s_c%s_t%s.tif' % (name,str(z),cName,str(t)), 'w')    
                tif_image.tile_image_params(sizeX,sizeY,sizeC,tileWidth,tileHeight,'lzw')
                for tileOffsetY in range(
                        0, ((sizeY + tileHeight - 1) / tileHeight)):
        
                    for tileOffsetX in range(
                            0, ((sizeX + tileWidth - 1) / tileWidth)):
        
                        x = tileOffsetX * tileWidth
                        y = tileOffsetY * tileHeight
                        w = tileWidth
        
                        if (w + x > sizeX):
                            w = sizeX - x
        
                        h = tileHeight
                        if (h + y > sizeY):
                            h = sizeY - y
                        
                        tile_count += 1
                        tile_data = next_tile()
                        if (h != tile_data.shape[1]) or (w != tile_data.shape[2]):
                            h = tile_data.shape[1]
                            w = tile_data.shape[2]
                        tile_dtype = tile_data.dtype
                        tile = np.zeros((1,tileWidth,tileHeight),dtype=tile_dtype)
                        tile[0,:h,:w] = tile_data[0,:,:]                     
                        tif_image.write_tile(tile,x,y)
                        
                tif_image.WriteDirectory()
                tif_image.close()

    return image_names

def download_pipeline(pipeline):
    
    filename = pipeline.getFile().getName()
    file_path = os.path.join(input_dir, filename)
    f = open(str(file_path), 'w')
    print( "\nDownloading file to", file_path, "..." )
    try:
        for chunk in pipeline.getFileInChunks():
            f.write(chunk)
    finally:
        f.close()
        print( "File downloaded!" )
    return file_path,filename

def datafile_name(pipeline_path):
    with open(pipeline_path) as f:
        for line in itertools.islice(f, 7, None):
            if 'Name of the file' in line:
                try:
                    idx = line.index(':')
                    datafile = line[idx+1:]
                except:
                    return None
    return datafile        

def write_csv_datafile(conn,image,image_names,pipeline):
    print( 'image_names', image_names)
    datafilename = datafile_name(pipeline)
    datafilepath = os.path.join(input_dir,datafilename)
    sizeZ = image.getSizeZ()
    sizeT = image.getSizeT()
    datafile = []
    for cName in image_names.iterkeys():
        row = []
        row.append('Image_FileName_%s' % cName)
        for im_name in image_names[cName]:
            row.append(im_name)
        datafile.append(row)
    print( 'datafile with image names:', datafile)
    
    for cName in image_names.iterkeys():
        row = []
        row.append('Series_%s' % cName)
        for z in range(sizeZ):        
            for t in range(sizeT):
                row.append(0)
        datafile.append(row) 
    print( 'datafile with frame numbers names:', datafile)
    
    for cName in image_names.iterkeys():
        row = []
        row.append('Frame_%s' % cName)        
        for z in range(sizeZ):
            row.append(0)
        datafile.append(row)        
    datafile = map(list, zip(*datafile))

    print( datafile )
         
    with open(datafilepath,'wb') as f:
        writer = csv.writer(f)
        writer.writerows(datafile)
        
    return datafilepath
 
def download_raw_planes(image, mode, channels, channel_names, region=None):
    """
    Download the specified image as a 'raw' tiff to local directory.
    The pixel type and pixel values of the tiffs will be limited to int32

    @param image:               BlitzGateway imageWrapper
    @param cIndex:              The channel being downloaded
    @param region:              Tuple of (x, y, width, height) if we want a region of the image
    """
    sizeZ = image.getSizeZ()
    sizeT = image.getSizeT()
    name,ext = os.path.splitext(image.getName())
    
    # We use getTiles() or getPlanes() to provide numpy 2D arrays for each image plane
    if region is not None:
        w = region[2]
        h = region[3]
        roi = region[:-1]
        zctList = []
        for z in range(sizeZ):
            for c in channels:
                for t in range(sizeT):
                    zctList.append((z,c,t,roi))
        planes = image.getPrimaryPixels().getTiles(zctList)    # A generator (not all planes in hand) 
    else:
        w = image.getSizeX()
        h = image.getSizeY()
        zctList = []
        for z in range(sizeZ):
            for c in channels:
                for t in range(sizeT):
                    zctList.append((z,c,t))
                    
        planes = image.getPrimaryPixels().getPlanes(zctList)    # A generator (not all planes in hand)                
    plane_list = []
    for i,p in enumerate(planes):
        plane_list.append(p)
        
    image_names = {}
    for chan in channel_names:
        image_names[chan] = []
                        
    if 'Fluorescence' in mode:

        p = 0
        for z in range(sizeZ):
            for i,c in enumerate(channels):
                cName = channel_names[i]
                for t in range(sizeT):
                    image_data = plane_list[p].astype('uint8')
                    im_name = '%s_z%s_c%s_t%s.tif' % (name,str(z),cName,str(t))
                    img = Image.fromarray(image_data)
                    img.save(os.path.join(input_dir,im_name))
                    image_names[cName].append(im_name)
                    p += 1
    
    elif 'Brightfield' in mode:

        p = 0
        image_data = zeros((h,w,3),dtype='uint8')
        print('image data shape:',image_data.shape)
        for z in range(sizeZ):
            for c in channels:
                for t in range(sizeT):
                    image_data[:,:,c] = plane_list[p]
                    p += 1
        
        if region is not None:            
            im_name = '%s_%s.tif' % (image.getId(),roi)
        else:
            im_name = '%s.tif' % (image.getId())
        img = Image.fromarray(image_data)
        img.save(os.path.join(input_dir,im_name))
        cName = channel_names[0]
        image_names[cName].append(im_name)      
            
    return image_names

def run_pipeline(pipeline,datafile=None):
        
    result_files = None

    try:
        cmd = '/usr/CellProfiler/CellProfiler.py'
        result = subprocess.check_call([sys.executable or 'python', cmd, '-b', '--do-not-fetch', '-c', '-r', '-i',
                                    input_dir, '-o', output_dir, '--data-file', datafile, '-p', pipeline])    
     
        if result:
            print("Execution failed with code: %d" % result, file=sys.stderr)
            message = 'Pipeline execution failed'
        elif not result:
            result_files = glob.glob('%s/*.csv' % output_dir)
            if result_files:
                message = 'pipeline executed successfully'  
            else:
                message = 'No results files found'
                return None,message
        return result_files,message
    except OSError, e:
        print("Pipeline execution failed:", e, file=sys.stderr)
        message = 'Exception encountered'
        return result_files,message

def upload_processed_images(conn,original_image,region,image_list,dataset,pipeline):
    """
    This creates a new Image in OMERO using all the images in destination folder as Z-planes
    """
    # Create a new Image in OMERO, with the jpeg images as a Z-stack.
    # We need a generator to produce numpy planes in the order Z, C, T.
    
    # Need to check whether we're dealing with RGB images (3 channels) or greyscale (1 channel)
  
    new_images = []
    new_image_ids = []
    for i in image_list:
        imageName = i
        tif = TiffFile(os.path.join(output_dir, i))
        img = imread(os.path.join(output_dir, i))
        dims = img.ndim
        if dims == 2:
            sizeZ = 1
            sizeC = 1
            sizeT = 1        
        if dims == 3:
            sizeZ = 1
            if tif.is_rgb:
                sizeC = 3
            else:
                sizeC = img.shape[0]
            sizeT = 1
        if dims == 4:
            sizeZ = img.shape[0]
            sizeC = img.shape[1]
            sizeT = 1
        if dims == 5:
            sizeZ = img.shape[0]
            sizeC = img.shape[1]
            sizeT = img.shape[2]
         
        def plane_generator():
            with TiffFile(os.path.join(output_dir, imageName)) as tif:
                if tif.is_rgb:
                    for i in range(3):
                        yield tif.asarray()[:,:,i]
                else:
                    for plane in tif:
                        yield plane.asarray()
                    
        # Create the image
        plane_gen = plane_generator()
        if region:
            roi_id = region[-1]
            basename = 'Image_%s_ROI_%s_%s' % (original_image.getId(),str(roi_id),os.path.basename(imageName))
            description = "Output from CellProfiler:\n  Pipeline: %s\n  ROI ID: %d\n Image ID: %d"\
                    % (pipeline, roi_id, original_image.getId())
        else:
            basename = 'Image_%s_%s' % (original_image.getId(),os.path.basename(imageName))
            description = "Output from CellProfiler:\n  Pipeline: %s\n Image ID: %d"\
                    % (pipeline, original_image.getId())            
        newImg = conn.createImageFromNumpySeq(plane_gen, basename,  sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT, 
                                              description=description, dataset=dataset)
        print( "New Image ID", newImg.getId())
        new_images.append(newImg)
        new_image_ids.append(newImg.getId() )
    return new_images,new_image_ids   

def upload_results(conn,objs,results):
    
    for obj in objs:
        for result in results:
            # create the original file and file annotation (uploads the file etc.)
            namespace = "qbi.cellprofiler"
            fileAnn = conn.createFileAnnfromLocalFile(result, mimetype="text/csv", ns=namespace, desc=None)
            obj.linkAnnotation(fileAnn)     # link it to dataset.

def do_processing(conn,image,image_names,mode,pipeline,channel_list,channel_names,dataset,region=None):
    """
    Run the pipeline on a single image (set)
    Download the image to the input folder
    """
    
    # create the datafile which tells the pipeline about the image and channels being processed
    datafile = write_csv_datafile(conn,image,image_names,pipeline)
    
    # Generate a stack of processed images from input tiffs.
    results,message = run_pipeline(pipeline,datafile)
    if results is None:
        return (None, message, None, None)
    
    # if the pipeline produces new images upload them from default output path
    print('output directory:',output_dir)
    files = glob.glob('%s/*.*' % output_dir)
    output_image_list = [f for f in files if (('.tif' in f) or ('.tiff' in f) or ('.png' in f))]
    print('output image list:',output_image_list)
    parentDataset = None
    new_images = []
    new_imageIds = []
    if output_image_list:
        if dataset is not None:
            parentDataset = dataset
        else:
            parentDataset = image.getParent()
            
        new_images,new_imageIds = upload_processed_images(conn,image,region,output_image_list,parentDataset,pipeline)
        
    # if the pipeline produces an output results file, upload it (annotate to original data)
    if results and parentDataset:
        upload_results(conn,new_images,results)
    elif results and (parentDataset is None):
        images = conn.getObjects("Image",[image.getId()]) # need an iteratable for upload_results
        upload_results(conn,images,results)         

    # handle return
    if (len(new_images) == 0) and results:
        message = "New CellProfiler results attached to image ID %s"%image.getId()
        return (None,message,results,new_imageIds)
    if len(new_images) == 1:
        new = new_images[0]
        msg = "New Image: %s " % new.getName()
        return (new._obj, msg, results, new_imageIds)
    else:
        ds = new_images[0].getParent()
        if ds is not None:
            return (ds._obj, "%s New Images in Dataset:" % len(new_images),results,new_imageIds)
        else:
            return (None, "Created %s New Images" % len(new_images),results,new_imageIds)   
        
def list_image_names(conn, image_ids, results):
    """
    Builds a list of the image names
    
    @param conn: The BlitzGateway connection
    @param ids: Python list of image ids
    """
    image_names = []
    for i,ids in enumerate(image_ids):
        for j,image_id in enumerate(ids):
            img = conn.getObject('Image', image_id)
            if not img:
                continue
    
            ds = img.getParent()
            if ds:
                pr = ds.getParent()
            else:
                pr = None
            
            filenames = [os.path.basename(result) for result in results[i]]
            image_names.append("[%s][%s] Image %d : %s : %s" % (
                               pr and pr.getName() or '-',
                               ds and ds.getName() or '-',
                               image_id, os.path.basename(img.getName()),
                               ','.join(filenames)))

    return image_names

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
    msg['Subject'] = '[OMERO Job] CellProfiler'
    msg.attach(MIMEText("""
New CellProfiler results files created:

Format:
[parent project/datset][cellprofiler results] image id : image name : result filename

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()        

def run_processing(conn,scriptParams):
    global input_dir
    global output_dir
    
    message = ""
    robj = None
    
    # get the annotation id of the pipeline
    pipeline = scriptParams['Pipeline']
    if not pipeline:
        message += 'Could not find specified annotation'
        return message
    
    # download the pipeline to the default input directory - this will also hold the images to be processed
    pipeline_path,pipeline_name = download_pipeline(pipeline)
    
    use_rois = scriptParams['Analyse_ROI_Regions']
    
    mode = scriptParams["Imaging_mode"]
    channel_names = scriptParams['Channel_names']
    if 'Brightfield' in mode:
        channel_list = [0,1,2]
    else:    
        channels = scriptParams['Channels_to_process']
        channel_list = [int(c) for c in channels]
    
    if scriptParams['New_Dataset']:
        datasetName = scriptParams['Container_Name']
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        dataset = conn.getUpdateService().saveAndReturnObject(dataset)
    else:
        dataset = None
    
    if 'Image' in scriptParams['Data_Type']:    
        image_ids = scriptParams['IDs']
    elif 'Dataset' in scriptParams['Data_Type']:
        image_ids = []
        dataset_id = scriptParams['IDs']
        dataset = conn.getObject("Dataset",dataset_id[0])
        if len(dataset_id) > 1:
            message += 'This script currently operates on a single dataset.'
        for image in dataset.listChildren():
            image_ids.append(image.getId())
            
    def empty_dir(dir_path):
        for old_file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, old_file)
            os.unlink(file_path)
            
    input_dir = tempfile.mkdtemp(prefix='default_cp_input')
    output_dir = tempfile.mkdtemp(prefix='default_cp_output')    
    new_image_ids = []
    results_files = []
    for image in conn.getObjects("Image", image_ids):
        
        # remove input and processed images
        empty_dir(input_dir)
        empty_dir(output_dir)
               
        if use_rois:
            for r in get_rects_from_rois(conn, image.getId()):
                empty_dir(input_dir)
                empty_dir(output_dir)
                width = r[2]
                height = r[3]
                if (width < 4096) and (height < 4096):
                        # download the images for processing
                    image_names = download_raw_planes(image, mode, channel_list, channel_names, r)
                    outputs = do_processing(conn,image,image_names,mode,pipeline_path,\
                                                                            channel_list,channel_names,\
                                                                            dataset,r)
                    robj, pipeline_message, results, newIds = outputs
                    message += pipeline_message
                else:
                    download_raw_planes_as_tiles(conn, image, channel_list, channel_names, r)
                    message += ' Skipped ROI %s' % r[-1]
                    continue
        else:
            width = image.getSizeX()
            height = image.getSizeY()
            if (width < 4096) and (height < 4096):
                        # download the images for processing
                image_names = download_raw_planes(image, mode, channel_list, channel_names)
                outputs = do_processing(conn,image,image_names,mode,pipeline_path,channel_list,\
                                                                        channel_names,dataset)
                print('outputs:',outputs)
                robj, pipeline_message, results, newIds = outputs
                message += pipeline_message
            else:
                download_raw_planes_as_tiles(conn, image, channel_list, channel_names, None)
                message += ' Skipped image %s' % image.getId()
                continue
                            
        new_image_ids.append(newIds)
        results_files.append(results)
        
    if scriptParams['Email_Results'] and new_image_ids:
        email_results(conn,scriptParams,new_image_ids,results_files)
    elif scriptParams['Email_Results']:
        image_ids = scriptParams['IDs']
        email_results(conn,scriptParams,image_ids,results_files)
               
    shutil.rmtree(input_dir)
    shutil.rmtree(output_dir)
    return robj, message  
    
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

    # Validate with a regular expression. Not perfect but it will do
    return re.match("^[a-zA-Z0-9._%-]+@[a-zA-Z0-9._%-]+.[a-zA-Z]{2,6}$",
                    userEmail)

def temporary_connection():
    temp_client = omero.client()
    omeroProperties = temp_client.getProperties().getPropertiesForPrefix('omero')
    # Configuration
    HOST = omeroProperties.get('omero.host', 'localhost')
    PORT = omeroProperties.get('omero.port', 4064)
    USERNAME = omeroProperties.get('omero.user')
    PASSWORD = omeroProperties.get('omero.pass')
    temp_conn = BlitzGateway(USERNAME, PASSWORD, host=HOST, port=PORT)
    connected = temp_conn.connect()
    return temp_client,temp_conn
    
def find_duplicate_annotations(mylist):
    D = defaultdict(list)
    for i,item in enumerate(mylist):
        D[item].append(i)
    return {k:v for k,v in D.items() if len(v)>1}
    
def get_user_annotations(extension='txt'):
    client,conn = temporary_connection()
    params = omero.sys.ParametersI()
    params.exp(conn.getUser().getId())  # only show current user's Datasets
    datasets = conn.getObjects("Dataset", params=params)
    annotations = []
    annotation_names = []
    for dataset in datasets:
        print_obj(dataset, 2)
        for dsAnn in dataset.listAnnotations():
            if isinstance(dsAnn, omero.gateway.FileAnnotationWrapper):
                print_obj(dsAnn.getFile(),4)
                annotations.append(dsAnn)
                annotation_names.append(dsAnn.getFile().getName())
        for image in dataset.listChildren():
            print_obj(image, 4)
            for imAnn in image.listAnnotations():
                if isinstance(imAnn, omero.gateway.FileAnnotationWrapper):
                    print_obj(imAnn.getFile(),4)
                    annotations.append(imAnn)
                    annotation_names.append(imAnn.getFile().getName())
    filtered_anns = [ann[0] for ann in zip(annotations,annotation_names) if extension in ann[1]]
    filtered_names = [rstring("ID:"+str(ann[0].getId())+" "+ann[1]) for ann in zip(annotations,annotation_names) if extension in ann[1]]
    duplicates = find_duplicate_annotations(filtered_names)
    for k,v in duplicates.iteritems():
        dups = v[1:]
        for d in dups:
            filtered_anns.pop(d)
            filtered_names.pop(d)
        
    client.closeSession()
    return filtered_anns,filtered_names

    
def runScript():
    """
    The main entry point of the script, as called by the client via the scripting
    service, passing the required parameters. 
    """
    anns,names = get_user_annotations(extension="cppipe")
    
    dataTypes = [rstring("Dataset"),rstring("Image")]
    channels = [rstring("0"),rstring("1"),rstring("2"),rstring("3"),rstring("4")]
    mode = [rstring("Fluorescence"),rstring("Brightfield")]
    
    client = scripts.client('CellProfiler_Pipeline.py',
"""
This script attempts to execute CellProfiler pipeline that has been saved to the 
server as an annotation. If the 'SaveImages' module is used, make sure to choose 
'tif' for the new images in your pipeline.
""",

    scripts.String("Data_Type", optional=False, grouping="1",
        description="The data you want to work with.", values=dataTypes, default="Image"),
                            
    scripts.List("IDs", optional=False, grouping="2",
        description="List of Image IDs for each channel being processed").ofType(rlong(0)),
    
    scripts.String("Pipeline", optional=False, grouping="3",values=names,
        description="Cellprofiler pipeline to execute"),
                            
    scripts.String("Imaging_mode", optional=False, grouping="4",
        description="The imaging modality of the data to be analysed", values=mode, default="Fluorescence"),
                            
    scripts.List("Channels_to_process", optional=False, grouping="5",
        description="channel to be processed in each image list",values=channels).ofType(rstring("")),
                           
    scripts.List("Channel_names", optional=False, grouping="6",
        description="channel names",).ofType(rstring("")),                               

    scripts.Bool("Analyse_ROI_Regions", grouping="7", default=False,
        description="Use Rectangle ROIs to define regions to analyse. By default analyse whole image"),

    scripts.Bool("New_Dataset", grouping="8",
        description="Put results in new dataset? Only do this if the pipeline creates new images",
        default=False),
                                                        
    scripts.String("Container_Name", grouping="8.1",
        description="Option: put Images in new Dataset with this name",
        default="Pipeline_results"),
                            
    scripts.Bool("Email_Results", grouping="9", default=False,
        description="E-mail the results"),
                            
    scripts.String("Email_address", grouping="9.1", description="Specify e-mail address"),                            

    authors = ["Daniel Matthews"],
    institutions = ["University of Queensland", "QBI"],
    contact = "d.matthews1@uq.edu.au",
    ) 
    
    try:
        session = client.getSession()
        scriptParams = {}

        conn = BlitzGateway(client_obj=client)

        # process the list of args above. 
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)
                
        #retrieve the index of the chosen pipeline from the names list
        pipeIdx = names.index(rstring(scriptParams['Pipeline']))
        #get the actual pipeline object at this index from the anns list
        scriptParams['Pipeline'] = anns[pipeIdx]
        
        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return
        
        robj,message = run_processing(conn, scriptParams)

        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))
    finally:
        client.closeSession()

if __name__ == "__main__":
    runScript()