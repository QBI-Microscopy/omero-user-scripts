#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
import omero.util.script_utils as script_utils
import omero.util.pixelstypetopython as pixels_type
from omero.util.tiles import *
from omero.model import *
import omero.cli
from omero.rtypes import wrap
from omero.model import DatasetI, ProjectI

import sys
import os
import re
import tempfile
import shutil
from numpy import zeros
import glob
from tifffile import TiffFile, imsave
from libtiff import TIFF, TIFFfile
from ome_metadata import OMEExporter

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

IMAGEJPATH = "/usr/local/Fiji.app" # Path to Fiji.app
ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'
input_dir = ''
output_dir = ''

def delete_tmp(tmp_dir):
    """
    Delete the temporary directory
    
    @param tmp_dir:    the path of the directory to be deleted
    """
    try:
        for name in glob.glob("%s/*" % tmp_dir):
            os.remove(name)
        os.rmdir(tmp_dir)
    except:
        pass    
    
def set_attributes(conn,parent_image,child):
    
    updateService = conn.getUpdateService()
    parent_pixels = parent_image.getPrimaryPixels()
    # note pixel sizes (if available) to set for the new images
    physicalSizeX = parent_pixels.getPhysicalSizeX()
    physicalSizeY = parent_pixels.getPhysicalSizeY()
    physicalSizeZ = parent_pixels.getPhysicalSizeZ()
    
    child_image = conn.getObject("Image", child.getId())
    
    # Get the BlitzGateway wrapped pixels and unwrap it
    pixelsWrapper = child_image.getPrimaryPixels()
    child_pixels = pixelsWrapper._obj
    
    if physicalSizeX is not None:
        child_pixels.setPhysicalSizeX( rdouble(physicalSizeX) )
        
    if physicalSizeY is not None:
        child_pixels.setPhysicalSizeY( rdouble(physicalSizeY) )
        
    if physicalSizeZ is not None:
        child_pixels.setPhysicalSizeZ( rdouble(physicalSizeZ) )
        
    ptype = parent_pixels.getPixelsType().getValue()
    print "pixels type:",ptype
    
    pix_min = 0
    pix_max = 255
    
    if (ptype == 'uint16'):
        pix_min = 0.0
        pix_max = 65535.0
        
    colors = []
    print "Channel rendering settings:"
    for ch in parent_image.getChannels():
        if '405' in ch.getLabel():
            color = (0,0,255)
        if '488' in ch.getLabel():
            color = (0,255,0)
        if '561' in ch.getLabel():
            color = (255,0,0)
        if '640' in ch.getLabel():
            color = (255,0,255)
        alpha = 255
        colors.append(color + (alpha,))   
    
    child_pixelsId = pixelsWrapper.getId()
    for theC in range(child_image.getSizeC()):
        rgba = colors[theC]
        script_util.resetRenderingSettings(conn.createRenderingEngine(), 
                                          child_pixelsId, theC, pix_min, pix_max, rgba) 
    
    updateService.saveObject(child_pixels)

def create_containers(conn,parent_image,child_image):
    
    updateService = conn.getUpdateService()
    parentDataset = parent_image.getParent()
    parentProject = parentDataset.getParent()
         
    if parentDataset is None:
        print "No dataset created or found for new images."\
            " Images will be orphans."
    else:
        dsLink = omero.model.DatasetImageLinkI()
        dsLink.parent = omero.model.DatasetI(
            parentDataset.getId(), False)
        dsLink.child = omero.model.ImageI(child_image.getId(), False)
        updateService.saveObject(dsLink)
    if parentProject and parentProject.canLink():
        # and put it in the   current project
        projectLink = omero.model.ProjectDatasetLinkI()
        projectLink.parent = omero.model.ProjectI(
            parentProject.getId(), False)
        projectLink.child = omero.model.DatasetI(
            parentDataset.id.val, False)
        updateService.saveAndReturnObject(projectLink) 

def print_obj(obj, indent=0):
    """
    Helper method to display info about OMERO objects.
    Not all objects will have a "name" or owner field.
    """
    print """%s%s:%s  Name:"%s" (owner=%s) Date:%s""" % (\
            " " * indent,
            obj.OMERO_CLASS,\
            obj.getId(),\
            obj.getName(),\
            obj.getOwnerOmeName(),\
            obj.getDate())

def get_new_image(conn):    
    log = glob.glob(output_dir + '/stdout.txt')
    with open(log[0],'r') as f:
        ids = f.readlines()
        
    image_id = int(ids[0])
    newImg = conn.getObject('Image',image_id)
    return newImg
    

def do_import(conn, session, filename, dataset=None, project=None):
    user = conn.getUser()
    group = conn.getGroupFromContext()
    print "Current group: ", group.getName()
    
    sessionId = session['ID']
    hostname = session['host']
    print 'session',session
    cli = omero.cli.CLI()
    cli.loadplugins()
    cli.invoke(["sessions", "login", "-s", "localhost", "-k", "%s" % sessionId], strict=True)
#     cli.invoke(["login", "%s@localhost" % user.getName(), "-w", "omero", "-C"], strict=True)
#     cli.invoke(["sessions", "group", group.getName()], strict=True)
    import_args = ["import"]
    if dataset is not None:
        dsId = create_containers(conn, dataset, project)
        import_args.extend(["-d", str(dsId)])
    import_args.append(filename)
    import_args.extend(["-s","localhost","-u","%s"%user.getName()])
    # redirect both stderr and stdout to file
    errlog = output_dir + "/stderr.txt"
    import_args.extend(["---errs",errlog])
    outlog = output_dir + "/stdout.txt"
    import_args.extend(["---file",outlog])
    print import_args
    cli.invoke(import_args, strict=True)
    
    # use stdout to get the id of the new image
    newImg = get_new_image(conn)
    return newImg

def run_imagej_macro(stitching_args):
    """
    Here we set-up the ImageJ macro and run it from the command line.
    We need to know the path to ImageJ jar.
    The macro text is written to the temp folder that we're running the script in,
    and the path to the macro is passed to the command line.
    
    @param image_name:      filename of image being processed
    @param stitching_args:  a list of arguments for stitching provided by the script gui
    """
    print 'stitching args',stitching_args
    
    stitching_script = """
import sys
import os
import glob
import time
import math
import shutil

import ij
from ij import IJ

from loci.formats import ImageReader,ImageWriter
from loci.formats import MetadataTools
from loci.common import RandomAccessInputStream
from loci.common import RandomAccessOutputStream
from loci.formats.tiff import TiffSaver

from ome.xml.model.primitives import PositiveInteger

    
def delete_slices(slices_dir):
    try:
        for name in glob.glob("{0}img*".format(slices_dir)):
            os.remove(name)
    except:
        pass 
    
def write_fused(output_path,channel,sizeZ):

    IJ.log("Writing fused data")

    # number of slices will determine filename format
    digits = "00"
    if sizeZ < 100:
        digits = "0"
    if sizeZ < 10:
        digits = ""

    # get the base metadata from the first fused image
    meta = MetadataTools.createOMEXMLMetadata()
    reader = get_reader(output_path+"img_t1_z{0}1_c1".format(digits),meta)
    reader.close()
    
    # reset some metadata
    meta.setPixelsSizeZ(PositiveInteger(sizeZ),0)
    meta.setChannelID("Channel:0:" + str(0), 0, 0)
    spp = channel['spp']
    meta.setChannelSamplesPerPixel(spp, 0, 0)
    name = channel['name']
    color = channel['color']
    meta.setChannelName(name,0,0)
    meta.setChannelColor(color,0,0)
        
    # determine the number of subsets that need to be written
    slices_per_subset = 200
    num_output_files = divmod(sizeZ,slices_per_subset)
    fpaths = []
    if num_output_files[0] == 0:
        nslices = [sizeZ]
        num_output_files = 1
        fpaths.append("{0}fused_C{1}.ome.tif".format(output_path,str(theC-1)))
    else:
        nslices = []
        for n in range(num_output_files[0]):
            nslices.append(slices_per_subset)

        if num_output_files[1] > 0:
            nslices.append(num_output_files[1])        
        
        for s in range(len(nslices)):
            fpaths.append("{0}fused_C{1}_subset{2}.ome.tif"format.(output_path,str(theC-1),str(s)))

    # setup a writer
    writer = ImageWriter()
    writer.setCompression('LZW')
    writer.setMetadataRetrieve(meta)
    writer.setId(fpaths[0])

    # write the slices, changing the output file when necessary
    theZ = 0
    for f in range(len(fpaths)):
        writer.changeOutputFile(fpaths[f])
        for s in range(nslices[f]):
            fpath = output_path+"img_t1_z{0}{1}_c1".format(digits,str(theZ+1))
            if (len(digits) == 1) and (theZ+1 > 9):
                fpath = output_path+"img_t1_z{0}_c1".format(str(theZ+1))
            if (len(digits) == 2) and (theZ+1 > 9):
                fpath = output_path+"img_t1_z0{0}_c1"format.(str(theZ+1))
            if (len(digits) == 2) and (theZ+1 > 99):
                fpath = output_path+"img_t1_z{0}_c1"format.(str(theZ+1))
            IJ.log("writing slice {0}"format.(os.path.basename(fpath)))
            m = MetadataTools.createOMEXMLMetadata()
            r = get_reader(fpath,m)
            writer.saveBytes(theZ,r.openBytes(0))
            r.close()
            theZ += 1
    writer.close()
    
def run_stitching(*args):
    
    IJ.run("Grid/Collection stitching", "type=[Grid: snake by rows] order=[Right & Down                ] "\
            "grid_size_x={0} grid_size_y={1} tile_overlap={2} first_file_index_i=0 "\
            "directory=[{3}] file_names=[{4}] "\
            "output_textfile_name=[{5}] fusion_method=[{6}] "\
            "regression_threshold={7} max/avg_displacement_threshold={8} "\
            "absolute_displacement_threshold={9} compute_overlap "\
            "computation_parameters=[Save memory (but be slower)] "\
            "image_output=[Write to disk] output_directory=[{10}]".format(args)
            
def channel_info(meta):
    sizeC = meta.getPixelsSizeC(0).getValue()
    channels = []
    for c in range(sizeC):
        chan_d = {}
        chan_d['spp'] = meta.getChannelSamplesPerPixel(0,c)
        chan_d['name'] = meta.getChannelName(0,c)
        chan_d['color'] = meta.getChannelColor(0,c)
        channels.append(chan_d)
    return channels
    
def get_reader(file, complete_meta):
    reader = ImageReader()
    reader.setMetadataStore(complete_meta)
    reader.setId(file)
    return reader
    
def run_script():
    
    gridX = %s
    gridY = %s
    tile_overlap = %s
    input_dir = %s
    results = %s
    fusion = %s
    reg_thresh = %s
    max_disp = %s
    abs_dip = %s
    output_dir = %s
    sizeZ = %s
    
    input_data = glob.glob("{0}*.ome.tif".format(input_dir))
    original_metadata = MetadataTools.createOMEXMLMetadata()
    reader = get_reader(input_data[0],original_metadata)
    reader.close()

    channels = channel_info(original_metadata)

    for z in range(sizeZ):
        tile_names = "{0}Z{1}_T{i}.ome.tif".format(input_dir,z)
        run_stitching(gridX,gridY,tile_overlap,input_dir,tile_names,\
                      results,fusion,reg_thresh,max_disp,abs_dip,\
                      output_dir)
    
    write_fused(input_dir,channels,sizeZ) # channel index starts at 1

    delete_slices(input_dir)
    
if __name__=='__main__':
    run_script()

""" % stitching_args

    script_path = "stitching.py"

    # write the macro to a known location that we can pass to ImageJ
    f = open(script_path, 'w')
    f.write(stitching_script)
    f.close()

    # Call ImageJ via command line, with macro ijm path & parameters
    cmd = "%s/ImageJ-linux64 --memory=8000m --headless %s" % (IMAGEJPATH, ijm_path)
    os.system(cmd)     

def run_stitching(conn,session,stitching_args):
    """
    Launches the Grid Stitching plugin and uplaods results
    
    @param conn:            the BlitzGateWay connection
    @param session:         dictionary containing the session ID and hostname
    @param stitching_args:  list of arguments for stitching provided by the script gui
    """ 
       
    images = glob.glob(input_dir + '/*.tif')
    print 'images',images
    
    run_imagej_macro(stitching_args)
    stitched = glob.glob('%s/*.tif' % output_dir)
    newImg = None
    if stitched:
        newImg = do_import(conn,session,stitched[0])
        
    return newImg
    
def download_tiles(conn,image,theC,theZ):
    # export each plane in the original image, or those
    # selected by the user, as OME-TIFF
     
    if theZ:
        slicesZ = [theZ]
    else:
        slicesZ = range(image.getSizeZ())
        
    num_tiles = image.getSizeT()
    image_names = []
    
    for z in slicesZ:
        for t in range(num_tiles):
            im_name = 'Z%s_T%s.ome.tif' % (z, t)
            exporter = OMEExporter(conn,image,input_dir,im_name,theZ=z,theC=c,theT=t)
            exporter.generate()
            image_names.append(im_name)

    return image_names

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

def email_results(conn,params,image_ids,file_anns):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """
    print params['Email_Results']
    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids, file_anns)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = '[OMERO Job] DAOSTORM'
    msg.attach(MIMEText("""
New daostorm results files created:

Format:
[parent project/datset][daostorm results] image id : image name : result filename

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()
    
def run_processing(conn, session, script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """ 
        
    global input_dir
    global output_dir

    input_dir = tempfile.mkdtemp(prefix='stitching_input')
    output_dir = tempfile.mkdtemp(prefix='stitching_output')
    
    def empty_dir(dir_path):
        for old_file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, old_file)
            os.unlink(file_path)
        
    message = ""
      
    dataType = script_params["Data_Type"]
    
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
    
    image_ids = [i.getId() for i in images]
    if len(image_ids) > 10:
        message = 'Max number of datasets for batch exceeded (10)'
        return message
    
    new_images = []
    new_ids = []
    for image in conn.getObjects("Image",image_ids):
        # remove input and processed images
        empty_dir(input_dir)
        empty_dir(output_dir)
        theC = None    
        if script_params['Single_Channel']:
            theC = script_params['Channel']

        theZ = None
        sizeZ = image.getSizeZ()
        if script_params['Single_Z']:
            theZ = script_params['Z_slice']
            sizeZ = 1
                     
        # download the image
        image_names = download_tiles(conn,image,theC,theZ)
                
        results_file = str(image.getId()) + "_stitching.txt"        
        
        stitching_args = (script_params['grid_x'], script_params['grid_y'], \
                          script_params['tile_overlap'], input_dir, results_file,\
                          script_params['fusion_method'], script_params['regression_threshold'], \
                          script_params['ave_displacement_threshold'], script_params['abs_displacement_threshold'],\
                          output_dir,sizeZ)
        
#         new_image = run_stitching(conn,image,channels,zslices,stitching_args,results_file)
        new_image = run_stitching(conn,session,image,theC,theZ,stitching_args,results_file)

        create_containers(conn,image, new_image)
                    
        if new_image:
#             set_attributes(conn,image, new_image)
            new_images.append(new_image)
            new_ids.append(new_image.getId())
     
        if len(new_ids) == 0:
            print "No new images created."
            return       
  
    if new_images:
        if len(new_images) > 1:
            message += "Created %s new images" % len(new_images)
        else:
            message += "Created a new image"
    else:
        message += "No image created"
        
    
    print script_params['Email_Results']
    if script_params['Email_Results'] and new_images:
        email_results(conn,script_params,new_ids)
        
    shutil.rmtree(input_dir)
    shutil.rmtree(output_dir)
# 
    robj = (len(new_images) > 0) and new_images[0]._obj or None
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
    print userEmail
    # Validate with a regular expression. Not perfect but it will do
    return re.match("^[a-zA-Z0-9._%-]+@[a-zA-Z0-9._%-]+.[a-zA-Z]{2,6}$",
                    userEmail)

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """
                
    dataTypes = [rstring("Dataset"),rstring("Image")]
    fusion_method = [rstring('Linear Blending'), rstring('Average'), rstring('Median'), \
                     rstring('Max. Intensity'),\
                     rstring('Min. Intensity'), rstring('Intensity of random input tile'),\
                     rstring('Do not fuse tiles (only write TileConfiguration')]

    client = scripts.client('Grid_Stitching.py', """Run the "Stitching" FIJI plugin.
MAXIMUM NUMBER OF DATASETS FOR BATCH IS FIVE!""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of images to be stitched").ofType(rlong(0)),
                            
    scripts.Bool("Single_Channel", grouping="03",default=False,
        description="Stitch all channels or a single channel? Uncheck for all channels"),
                                                        
    scripts.Int("Channel", grouping="03.1",
        description="channel to be stitched"),

    scripts.Bool("Single_Z", grouping="04",default=False,
        description="Stitch all z slices or a single slice? Uncheck for all z slices"),
                                                        
    scripts.Int("Z_slice", grouping="04.1",
        description="z-slice to be stitched"),
                                                        
    scripts.Int("grid_x", optional=False, grouping="05", default=2,
        description="how many tiles in the x-direction"),

    scripts.Int("grid_y", optional=False, grouping="06", default=2,
        description="how many tiles in the y-direction"),      
                            
    scripts.Int("tile_overlap", optional=False, grouping="06",default=20,
        description="percentage overlap between tiles"),  
                            
    scripts.String("fusion_method", optional=False, grouping="07",default='Linear Blending',
        description="method used to fuse the tiles", values=fusion_method),
                        
    scripts.Float("regression_threshold", optional=False, grouping="08",default=0.3,
        description="global optimisation parameter"),  

    scripts.Float("ave_displacement_threshold", optional=False, grouping="09",default=2.5,
        description="global optimisation parameter"),

    scripts.Float("abs_displacement_threshold", optional=False, grouping="10",default=3.5,
        description="global optimisation parameter"),                                             
                            
    scripts.Bool("Email_Results", grouping="11", default=False,
        description="E-mail the results"),
                            
    scripts.String("Email_address", grouping="11.1", description="Specify e-mail address"),                                       
        
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
                
        session = {}
        session['ID'] = client.getSessionId()
        session['host'] = client.getProperty('omero.host')
                
        print scriptParams
        
        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)

        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return
        
        # process images in Datasets
        robj,message = run_processing(conn, session, scriptParams)
        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()