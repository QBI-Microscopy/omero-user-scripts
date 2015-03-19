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
from omero.util.OmeroPopo import PolygonData 

import os
import re
import tempfile
import shutil
import glob
import subprocess
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
    
def get_polygons_from_rois(conn, image):
    """ 
    Returns a list of (x, y, width, height), one for each ROI with rectangle shape

    @param conn:        BlitzGateway connection
    @param imageId:     Image ID
    """

    # Using the underlying ROI service & omero.model objects (no ROI support in Blitz Gateway yet)
    roiService = conn.getRoiService()
    imageId = image.getId()
    result = roiService.findByImage(imageId, None)
    updateService = conn.getUpdateService()
    polygons = []
    rects = []
    for roi in result.rois:
        # go through all the shapes of the ROI
        roi_id = roi.getId().getValue()
        for shape in roi.copyShapes():
            if shape.__class__.__name__ == 'PolygonI':
                poly = PolygonData(shape)
                rect = poly.getBoundingRectangle()
                points = poly.fromPoints("points")
                print 'points',type(points)
                print 'rect',rect
                x = rect[0][0]
                y = rect[0][1]
                w = rect[1][0] - rect[0][0]
                h = rect[1][1] - rect[0][1] 
                rects.append( (x,y,w,h,roi_id) )
                polygons.append(poly)
                break

    return rects,polygons

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
        datasetObj = updateService.saveObject(dsLink)   

def get_new_image(conn):    
    log = glob.glob(output_dir + '/stdout.txt')
    with open(log[0],'r') as f:
        ids = f.readlines()
        
    image_id = int(ids[0])
    newImg = conn.getObject('Image',image_id)
    return newImg
    

def do_import(conn, session, filename, dataset=None, project=None):
    user = conn.getUser()
    
    sessionId = session['ID']
    cli = omero.cli.CLI()
    cli.loadplugins()
    cli.invoke(["sessions", "login", "-s", "localhost", "-k", "%s" % sessionId], strict=True)
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
    cli.invoke(import_args, strict=True)
    
    # use stdout to get the id of the new image
    newImg = get_new_image(conn)
    return newImg

def run_clearing(conn, session, omero_image, input_image):
    
    cleared = "Image%s_clear.ome.tif" % omero_image.getId()
    output_path = os.path.join(output_dir,cleared)
    print 'output path:',output_path
    clearing_script = """
import sys
from ij import IJ as ij
from ij.plugin.frame import RoiManager
from ij.gui import Roi, PolygonRoi 

from loci.plugins import BF
from loci.common import Region
from loci.plugins.in import ImporterOptions
from loci.formats import ImageReader, ImageWriter
from loci.formats import MetadataTools
from ome.xml.meta import OMEXMLMetadata

file = "/fiji/input/%s"

options = ImporterOptions()
options.setId(file)
imps = BF.openImagePlus(options)

reader = ImageReader()
omeMeta = MetadataTools.createOMEXMLMetadata()
reader.setMetadataStore(omeMeta)
reader.setId(file)

roiCount = omeMeta.getROICount()

if roiCount > 1:
    sys.exit(0)

omeMetaStr =  omeMeta.dumpXML()
shape = omeMeta.getShapeType(0,0)

if ('Polyline' not in shape):
    sys.exit(0)

prefix = omeMetaStr.index(shape)
stop = omeMetaStr.find('/',prefix,-1)    - 1
start = len(shape + " " + "points=") + 1

pts = omeMetaStr[start+prefix:stop]

new_pts_str =pts.replace(" ",",")
new_pts = [int(p) for p in new_pts_str.split(",")]

xs = new_pts[0::2]
ys = new_pts[1::2]

proi = PolygonRoi(xs, ys, len(xs), Roi.POLYGON)  
imp = imps[0]
imp.setRoi(proi)

# create a writer and set metadata
writer = ImageWriter()
writer.setMetadataRetrieve(omeMeta)
writer.setId('/fiji/output/%s')

# get the stack
planes = imp.getStack()
for p in range(planes.getSize()):

    # get the plane
    plane = planes.getProcessor(p+1)
    
    # fill outside
    plane.fillOutside(proi)
    
    pixels = plane.convertToByte(True).getPixels()
    writer.saveBytes(p,pixels)
    
reader.close() 
writer.close()
imp.flush()""" % (input_image,cleared)
    
    script = "clearing.py"
    script_path = input_dir + "/%s"%script

    # write the script to a known location that we can pass to ImageJ
    f = open(script_path, 'w')
    f.write(clearing_script)
    f.close()

    # call dockerized Fiji
    cmd = "docker run --rm -v %s:/fiji/input -v %s:/fiji/output \
fiji/fiji:latest fiji-linux64 --memory=8000m --headless \
'/fiji/input/%s'"%(input_dir,output_dir,script)
    print "docker command",cmd
    os.system(cmd)
         
    newImg = None
    # check for new image
    cleared = glob.glob('%s/*.tif' % output_dir)
    if cleared:
        # if it exists upload it
        newImg = do_import(conn,session,cleared[0])

    return newImg
    
def download_image(conn,image, polys):
    
    im_name = 'Image%s.ome.tif' % (image.getId())
    exporter = OMEExporter(conn,image,input_dir,im_name,ROI=polys)
    exporter.generate()
    im_path = os.path.join(input_dir,im_name)
    return im_name,im_path

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

    input_dir = tempfile.mkdtemp(prefix='clearing_input')
    output_dir = tempfile.mkdtemp(prefix='clearing_output')
    
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
    for source in conn.getObjects("Image",image_ids):
        # remove input and processed images
        empty_dir(input_dir)
        empty_dir(output_dir)
        
        # get the polygon rois
        rects,polys = get_polygons_from_rois(conn, source)
                    
        # download the image and write polys to metadata
        target,target_path = download_image(conn,source,polys)
        
        # run the clearing
        new_image = run_clearing(conn,session,source,target)
        
        # put images in datasets, datasets in projects
        create_containers(conn, source, new_image)
                    
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

    client = scripts.client('Clear_Outside.py', """Run the "Clear Outside" ImageJ processing on
polygon region of interest.""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of images to be cleared").ofType(rlong(0)),                                            
                            
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