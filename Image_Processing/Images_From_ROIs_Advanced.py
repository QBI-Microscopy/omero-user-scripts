#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject, rdouble
import omero.scripts as scripts
import omero.util.script_utils as script_utils
from omero.util.tiles import *
from omero.model import *
import omero.util.script_utils as script_util
from omero.util.OmeroPopo import PolygonData 
from omero.rtypes import wrap
import omero.cli
from omero.model import DatasetI, ProjectI

import os
import glob
import tempfile
from ome_metadata import OMEExporter
import shutil

from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib

ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'
IMAGEJPATH = "/usr/local/Fiji.app" # Path to Fiji.app
input_dir = ""
output_dir = ""

def list_image_names(conn, ids):
    """
    Builds a list of the image names
    
    @param conn: The BlitzGateway connection
    @param ids: Python list of image ids
    """
    image_names = []
    for image_id in ids:
        img = conn.getObject('Image', image_id)
        if not img:
            continue

        ds = img.getParent()
        if ds:
            pr = ds.getParent()
        else:
            pr = None

        image_names.append("[%s][%s] Image %d : %s" % (
                           pr and pr.getName() or '-',
                           ds and ds.getName() or '-',
                           image_id, os.path.basename(img.getName())))

    return image_names

def email_results(conn,params,image_ids):
    """
    E-mail the result to the user.

    @param conn: The BlitzGateway connection
    @param params: The script parameters
    @param image_ids: A python list of the new image omero ids
    """
    print params['Email_Results']
    if not params['Email_Results']:
        return

    image_names = list_image_names(conn, image_ids)

    msg = MIMEMultipart()
    msg['From'] = ADMIN_EMAIL
    msg['To'] = params['Email_address']
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = '[OMERO Job] Slide Scanner image cropping'
    msg.attach(MIMEText("""
New images created from ROIs:

Format:
[parent project/datset][child dataset] new image id : parent image name

------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))

    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()    
    
def run_clearing(conn, session, image, input_path, originx, originy):
    """
    Run 'Clear Outside' in Fiji

    @param conn: The BlitzGateway connection
    @param session: A dictionary containing the ID and hostname
    @param input_path: The full path to the image being cleared
    @param originx: x position of bounding box in original image
    @param originy: y position of bounding box in original image
    """    
    
    image_name = os.path.basename(input_path)
    output_path = os.path.join(output_dir,image_name)
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
xs = [x-%d for x in xs]
ys = new_pts[1::2]
ys = [y-%d for y in ys]

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
imp.flush()""" % (image_name,originx,originy,image_name)

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
    
    cleared = glob.glob('%s/*.tif' % output_dir)
    if cleared:
        return cleared[0]
    else:
        return None

def get_rects_from_rois(conn, imageId):
    """ 
    Returns a list of (x, y, width, height), one for each ROI with rectangle shape

    @param conn:        BlitzGateway connection
    @param imageId:     Image ID
    """

    # Using the underlying ROI service & omero.model objects (no ROI support in Blitz Gateway yet)
    roiService = conn.getRoiService()
    result = roiService.findByImage(imageId, None)
    parent_image = conn.getObject("Image",imageId)
    sizeX = parent_image.getSizeX()
    sizeY = parent_image.getSizeY()
    print "image size x, y: ", sizeX,sizeY
    rects = []
    polygons = []
    for roi in result.rois:
        # go through all the shapes of the ROI
        roi_id = roi.getId().getValue()
        for shape in roi.copyShapes():
            name = shape.__class__.__name__
            if name == 'RectI':
                x = shape.getX().getValue()     # Need getValue() for omero.model rtypes
                y = shape.getY().getValue()
                w = shape.getWidth().getValue()
                h = shape.getHeight().getValue()
                
                if x < 0:
                    x = 0
                if y < 0:
                    y = 0
                if x > sizeX:
                    x = sizeX
                if y > sizeY:
                    y = sizeY
                if (x + w) > sizeX:
                    w = sizeX - x
                if (y + h) > sizeY:
                    h = sizeY - y
                print "roi x, y, w, h: ",x,y,w,h    
                rects.append( (x,y,w,h,roi_id,name) )
                break    # Only use the first Rect we find per ROI 
            if name == 'PolygonI':
                poly = PolygonData(shape)
                rect = poly.getBoundingRectangle()
                x = rect[0][0]
                y = rect[0][1]
                w = rect[1][0] - rect[0][0]
                h = rect[1][1] - rect[0][1] 
                
                if x < 0:
                    x = 0
                if y < 0:
                    y = 0
                if x > sizeX:
                    x = sizeX
                if y > sizeY:
                    y = sizeY
                if (x + w) > sizeX:
                    w = sizeX - x
                if (y + h) > sizeY:
                    h = sizeY - y
                print "roi x, y, w, h: ",x,y,w,h     
                rects.append( (x,y,w,h,roi_id,name) )
                polygons.append(poly)
                break         
                 
    return rects, polygons

def create_containers(conn, dataset, project=None):
    """
    Creates containers with names provided if they don't exist already.
    Returns Dataset ID.
    """
    #sessionId = cli._event_context.sessionUuid
    #conn = BlitzGateway(host='localhost')
    #conn.connect(sUuid = sessionId)
    params = omero.sys.Parameters()
    params.theFilter = omero.sys.Filter()
    params.theFilter.ownerId = wrap(conn.getUser().getId())

    d = None
    prId = None
    if project is not None:
#         p = conn.getObject("Project", attributes={'name': project}, params=params)
        p = conn.getObject("Project", project.getId()) 
        if p is None:
            print "Creating Project:", project
            p = omero.model.ProjectI()
            p.name = wrap(project)
            prId = conn.getUpdateService().saveAndReturnObject(p).id.val
        else:
            print "Using Project:", project, p
            prId = p.getId()
            # Since Project already exists, check children for Dataset
            for c in p.listChildren():
                if c.getName() == dataset:
                    d = c

    if d is None:
#         d = conn.getObject("Dataset", attributes={'name': dataset}, params=params)
        d = conn.getObject("Dataset", dataset.getId())  

    if d is None:
        print "Creating Dataset:", dataset
        d = omero.model.DatasetI()
        d.name = wrap(dataset)
        dsId = conn.getUpdateService().saveAndReturnObject(d).id.val
        if prId is not None:
            print "Linking Project-Dataset..."
            link = omero.model.ProjectDatasetLinkI()
            link.child = omero.model.DatasetI(dsId, False)
            link.parent = omero.model.ProjectI(prId, False)
            conn.getUpdateService().saveObject(link)
    else:
        print "Using Dataset:", dataset, d
        dsId = d.getId()
    return d,dsId


def get_new_image(conn):
    """ 
    Retrieved the ID of the new image from stdout.
    
    @param conn: The BlitzGateway connection
    """    
    log = glob.glob(output_dir + '/stdout.txt')
    with open(log[0],'r') as f:
        ids = f.readlines()
        
    image_id = int(ids[0])
    newImg = conn.getObject('Image',image_id)
    return newImg
    

def do_import(conn, session, filename, dataset=None, project=None):
    """
    Import the new image to OMERO using the command line importer
    
    @param conn: The BlitzGateway connection
    @param session: A dictionary containing the session ID and hostname
    @param filename: The path of the image being imported
    @param dataset: The dataset into which the new image is being placed
    @param project: The project into which the dataset is being placed
    """
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

def process_image(conn, parent_id, script_params, session):
    """
    Makes a new image from an ROI on a parent image. If the
    ROI is a PolygonI and the user chooses to 'clear outside'
    a script is run via Fiji to run the clearing. Otherwise the
    new image is created from the bounding box of the PolygonI
    
    @param conn: The BlitzGateway connection
    @param parent_id: The OMERO ID of the parent image
    @param script_params: The parameters required to run the script
    @param session: A dictionary containing session ID and hostname
    """ 
    
    global input_dir
    global output_dir
    input_dir = tempfile.mkdtemp(prefix='tiling_input')    
    output_dir = tempfile.mkdtemp(prefix='tiling_output')

    def empty_dir(dir_path):
        for old_file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, old_file)
            os.unlink(file_path)
                
    updateService = conn.getUpdateService()
    parent_image = conn.getObject("Image", parent_id)
    parentDataset = parent_image.getParent()
    parentProject = parentDataset.getParent()
    rects,polys = get_rects_from_rois(conn, parent_id)
    
    # Get the channels
    channels = None
    if script_params["Select_Channels"]:
        channels = script_params["Channels"]
        
    children = []
    child_ids = []
    # loop over bounding rectangles - 
    # could have originated from a polygon ROI
    for i,r in enumerate(rects):
        
        # so check the shape
        
        empty_dir(input_dir)
        empty_dir(output_dir)
        
        xbox, ybox, wbox, hbox, rid, shape = r
        print 'shape:',shape
        box = r[:-1]
        child_name ='ImageID%s_ROI%s.ome.tif'%(parent_image.getId(),rid)
        child_path = os.path.join(input_dir,child_name)
        
        if script_params['Clear_Outside_Polygon'] and ('PolygonI' in shape):
            exporter = OMEExporter(conn,parent_image,input_dir,child_name,\
                                   box,theC=channels,ROI=polys)
            exporter.generate()
            child_path = run_clearing(conn, session, parent_image, \
                                      child_path, xbox, ybox)
        elif ('PolygonI' in shape):
            # retain the PolygonI in the metadata
            exporter = OMEExporter(conn,parent_image,input_dir,child_name,\
                                   box,theC=channels,ROI=polys)
            exporter.generate() 
        elif ('RectI' in shape):
            # do not write ROI to metadata
            exporter = OMEExporter(conn,parent_image,input_dir,child_name,\
                                   box,theC=channels)
            exporter.generate()            
        
        print 'child_path:',child_path
        newImg = do_import(conn,session,child_path)
        
        children.append(newImg)
        child_ids.append(newImg.getId())
    
        if len(child_ids) == 0:
            print "No new images created."
            return
        
    if script_params['New_Dataset'] and \
       len(script_params['Container_Name'].strip()) > 0:
        # create a new dataset for new images
        datasetName = script_params['Container_Name']
        print "\nMaking Dataset '%s' of Images from ROIs of Image: %s" \
            % (datasetName, parent_id)
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        desc = "Images in this Dataset are from ROIs of parent Image:\n"\
            "  Name: %s\n  Image ID: %d" % (parent_image.getName(), parent_id)
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
        for cid in child_ids:
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
            
    shutil.rmtree(input_dir)
    shutil.rmtree(output_dir)
    
    return children, dataset, link, child_ids

def run_processing(conn, session, script_params):
    """
    Processes the list of Image_IDs, either making a new image-stack or a new
    dataset from each image, with new image planes coming from the regions in
    Rectangular ROIs on the parent images.
    
    @param conn: The BlitzGateway connection
    @param session: A dictionart containing session ID and hostname
    @param script_params: The script input parameters
    """

    dataType = script_params["Data_Type"]

    message = ""
    

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
    images = [image for image in images if (image.getROICount("Rect") > 0) or (image.getROICount("Polygon") > 0)]
    if not images:
        message += "No Rectangle or Polygon ROI found."
        return None, message

    total_rects = sum([i.getROICount("Rect") for i in images])
    total_polys = sum([i.getROICount("Polygon") for i in images])
    if total_rects + total_polys > 10:
        message += "Cannot start batch processing - too many rois (maximum is 10)."
        return None, message
        
    imageIds = [i.getId() for i in images]
    newImages = []
    newDatasets = []
    links = []
    for iId in imageIds:
        newImage, newDataset, link, new_ids = process_image(conn, iId, script_params, session)
        if newImage is not None:
            if isinstance(newImage, list):
                newImages.extend(newImage)
            else:
                newImages.append(newImage)
        if newDataset is not None:
            newDatasets.append(newDataset)
        if link is not None:
            if isinstance(link, list):
                links.extend(link)
            else:
                links.append(link)

    if newImages:
        if len(newImages) > 1:
            message += "Created %s new images" % len(newImages)
        else:
            message += "Created a new image"
    else:
        message += "No image created"

    if newDatasets:
        if len(newDatasets) > 1:
            message += " and %s new datasets" % len(newDatasets)
        else:
            message += " and a new dataset"
    
    print script_params['Email_Results']
    if script_params['Email_Results'] and (newImages or newDatasets):
        email_results(conn,script_params,new_ids)

    if not links or not len(links) == len(newImages):
        message += " but some images could not be attached"
    message += "."

    robj = (len(newImages) > 0) and newImages[0]._obj or None
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

def runScript():
    """
    The main entry point of the script, as called by the client via the scripting
    service, passing the required parameters. 
    """
       
    dataTypes = [rstring("Dataset"),rstring("Image")]
    
    client = scripts.client('Images_From_ROIs_Advanced.py',"""A script for making new
images from ROIs on a parent image. The ROI can be a rectangle or polygon and it is 
possible to clear the region outside a polygon ROI. This is replacement of the built-in
"Images_From_ROIs.py" script to better handle creation of regions > 4096 and to allow
creation of arbitrary shaped regions.
""",

    scripts.String(
        "Data_Type", optional=False, grouping="1",
        description="The data you want to work with.", 
        values=dataTypes, default="Image"),
                            
    scripts.List(
        "IDs", optional=False, grouping="2",
        description="List of Image IDs for each channel being processed").ofType(rlong(0)),

    scripts.Bool(
        "Clear_Outside_Polygon", grouping="3", default=False,
        description="Cleared area outside of polygon ROI?"),
    
    scripts.Bool(
        "Select_Channels", grouping="4", default=False,
        description="Specify channels?"),
                                                        
    scripts.List(
        "Channels", grouping="4.1",
        description="A list of channels to extract - integers channel numbers starting at 0").ofType(rlong(0)),
                            
    scripts.Bool(
        "New_Dataset", grouping="5", default=False,
        description="Make a new dataset for the ROIs"),
                                                        
    scripts.String(
        "Container_Name", grouping="5.1",
        description="Option: put Images in new Dataset with this name",
        default="From_ROIs"),
                        
    scripts.Bool(
        "Email_Results", grouping="6", default=False,
        description="E-mail the results"),
                        
    scripts.String("Email_address", grouping="6.1",
    description="Specify e-mail address"), 
                            
    authors = ["Daniel Matthews"],
    institutions = ["University of Queensland", "QBI"],
    contact = "d.matthews1@uq.edu.au",
    ) 
    
    try:
        session = {}
        session['ID'] = client.getSessionId()
        session['host'] = client.getProperty('omero.host')
        
        scriptParams = {}

        conn = BlitzGateway(client_obj=client)

        # process the list of args above. 
        for key in client.getInputKeys():
            if client.getInput(key):
                scriptParams[key] = client.getInput(key, unwrap=True)
        
        robj,message = run_processing(conn, session, scriptParams)
        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))
    finally:
        client.closeSession()

if __name__ == "__main__":
    runScript()