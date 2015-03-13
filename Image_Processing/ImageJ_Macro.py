#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero
from omero.gateway import BlitzGateway
from omero.rtypes import rstring, rlong, robject
import omero.scripts as scripts
import os
import re
import sys
import glob
import subprocess
import tempfile
from tifffile import TiffFile,imsave
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
import smtplib
 
IMAGEJPATH = "/usr/local/Fiji.app" # Path to Fiji.app
 
ADMIN_EMAIL = 'admin@omerocloud.qbi.uq.edu.au'
input_dir = ''
output_dir = ''

def download_raw_planes(conn, image):
    """
    Extracts the images from OMERO.
  
    @param conn:   The BlitzGateway connection
    @param images: The list of images
    """
    name = '%s/%s.ome.tif' % (input_dir, image.getId())
    e = conn.createExporter()
    e.addImage(image.getId())
  
    # Use a finally block to ensure clean-up of the exporter
    try:
        e.generateTiff()
        out = open(name, 'wb')
  
        read = 0
        while True:
            buf = e.read(read, 1000000)
            out.write(buf)
            if len(buf) < 1000000:
                break
            read += len(buf)
  
        out.close()
    finally:
        e.close()
  
    return name

# def download_raw_planes(image, region=None):
#     """
#     Download the specified image as a 'raw' tiff to local directory.
#     The pixel type and pixel values of the tiffs will be limited to int32
# 
#     @param image:               BlitzGateway imageWrapper
#     @param cIndex:              The channel being downloaded
#     @param region:              Tuple of (x, y, width, height) if we want a region of the image
#     """
# 
#     sizeZ = image.getSizeZ()
#     sizeC = 1#image.getSizeC()
#     sizeT = image.getSizeT()
#     name,ext = os.path.splitext(image.getName())
#     
#     # We use getTiles() or getPlanes() to provide numpy 2D arrays for each image plane
#     if region is not None:
#         zctList = []
#         for z in range(sizeZ):
#             for c in range(sizeC):
#                 for t in range(sizeT):
#                     zctList.append((z,c,t,region))
#     else:
#         zctList = []
#         for z in range(sizeZ):
#             for c in range(sizeC):
#                 for t in range(sizeT):
#                     zctList.append((z,c,t))
#                     
#     planes = image.getPrimaryPixels().getPlanes(zctList)    # A generator (not all planes in hand)                
#     plane_list = []
#     for i,p in enumerate(planes):
#         plane_list.append(p)
#     c = 0
#     for z in range(sizeZ):
#         for c in range(sizeC):
#             for t in range(sizeT):
#                 image_data = plane_list[c]
#                 im_name = '%s_z%s_c%s_t%s.tif' % (name,str(z),str(c),str(t))
#                 imsave(os.path.join(input_dir,im_name),image_data)
#                 c += 1
#             
#     return im_name

def upload_results(conn,objs,results):
     
    for obj in objs:
        for result in results:
            # create the original file and file annotation (uploads the file etc.)
            namespace = "qbi.imagej"
            print "\nCreating an OriginalFile and FileAnnotation"
            fileAnn = conn.createFileAnnfromLocalFile(result, mimetype="text/plain", ns=namespace, desc=None)
            obj.linkAnnotation(fileAnn)     # link it to dataset.

def run_imagej_macro(conn, input_path, ijm_path, macro_args, result_file):
    """
    Here we set-up the ImageJ macro and run it from the command line.
    We need to know the path to ImageJ jar.
    The macro text is written to the temp folder that we're running the script in,
    and the path to the macro is passed to the command line.
    """
    result_files = None
    
    # Run ImageJ
    try:
#        # Call ImageJ via command line, with macro ijm path & parameters
        output_path = output_dir + '/'
        default_args = [input_path, output_path]
        #default_args = [input_dir, output_path]
        macro_args = "*".join( default_args + macro_args)        # can't use ";" on Mac / Linu. Use "*"
        #     cmd = "%s/ImageJ-macosx --memory=1000m --headless -macro %s %s -batch" % (IMAGEJPATH, ijm_path, macro_args)
        cmd = "%s/ImageJ-linux64 --memory=1000m --headless -macro %s %s -batch" % (IMAGEJPATH, ijm_path, macro_args)
        
        print "Script command = %s" % cmd
        
        if result_file:
            logfile = open(result_file, 'wb')
        else:
            logfile = None
             
        # Run the command
        result = subprocess.call(cmd, stdout=logfile, shell=True)
        if result:
            print >>sys.stderr, "Execution failed with code: %d" % result
            message = 'Pipeline execution failed'
        elif not result:
            result_files = glob.glob('%s/*.txt' % output_dir)
            if result_files:
                message = 'Macro executed successfully'  
            else:
                message = 'No results files found'
                return None,message
        return result_files,message
 
    except OSError, e:
        print >>sys.stderr, "Execution failed:", e
        message = 'Exception encountered'
        return result_files,message

def upload_processed_images(conn,original_image,image_list,dataset,macro):
    """
    This creates a new Image in OMERO using all the images in destination folder as Z-planes
    """
     
    new_images = []
    new_image_ids = []
    for i in image_list:
        imageName = i
        tif = TiffFile(os.path.join(output_dir, i))
        sizeT, sizeZ, sizeC, image_height, image_width = tif.series[0]['shape']
         
        print 'image sizes:',sizeZ, sizeT, sizeC, image_height, image_width    
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
        basename = os.path.basename(imageName)
        description = "Output from ImageJ:\n  Macro: %s\n  Image ID: %d"\
                % (os.path.basename(macro), original_image.getId())
        newImg = conn.createImageFromNumpySeq(plane_gen, basename,  sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT, 
                                              description=description, dataset=dataset)
        print "New Image ID", newImg.getId()
        new_images.append(newImg)
        new_image_ids.append(newImg.getId() )
    return new_images,new_image_ids      

def download_macro(ann):
    """
        Downloads the specified file to and returns the path on the server
    """ 
    global mpath
    mpath = tempfile.mkdtemp(prefix='imagej_macro')
     
    if not os.path.exists(mpath):
        os.makedirs(mpath)
    file_name = ann.getFile().getName()
    file_path = os.path.join(mpath, file_name)
    f = open(str(file_path), 'w')
    print "\nDownloading file to", file_path, "..."
    try:
        for chunk in ann.getFileInChunks():
            f.write(chunk)
    finally:
        f.close()
        print "File downloaded!"
    return file_name,file_path

def process_image(conn, image, macro, macro_args, results_file, dataset):
    """
    Run the macro for a single image
    """
#     download the images for processing
    image_name = download_raw_planes(conn, image)
#     image_name = download_raw_planes(image)
    # Generate a stack of processed images from input tiffs.
    results,message = run_imagej_macro(conn, image_name, macro, macro_args, results_file)
     
    # if the pipeline produces new images upload them from default output path
    output_image_list = glob.glob(('%s/*.tif' % output_dir) or ('%s/*.png' % output_dir))
    print 'output_dir',output_dir
    print 'output_image_list:',output_image_list
 
    parentDataset = None
    new_images = []
    new_imageIds = []
    if output_image_list:
        if dataset is not None:
            parentDataset = dataset
        else:
            parentDataset = image.getParent()
        new_images,new_imageIds = upload_processed_images(conn,image,output_image_list,parentDataset,macro)
         
    # if the pipeline produces an output results file, upload it (annotate to original data)
    if results and parentDataset:
        upload_results(conn,new_images,results)
    elif results:
        images = conn.getObjects("Image",[image.getId()]) # need an iteratable for upload_results
        upload_results(conn,images,results)         
 
    # handle return
    if (len(new_images) == 0) and results:
        message = "New ImageJ results attached to image ID %s"%image.getId()
        return None,message,results,new_imageIds
    if len(new_images) == 1:
        new = new_images[0]
        msg = "New Image: %s" % new.getName()
        return new._obj, msg, results, new_imageIds
    else:
        ds = new_images[0].getParent()
        if ds is not None:
            return ds._obj, "%s New Images in Dataset:" % len(new_images),results,new_imageIds
        else:
            return None, "Created %s New Images" % len(new_images),results,new_imageIds   

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
    msg['Subject'] = '[OMERO Job] ImageJ'
    msg.attach(MIMEText("""
New ImageJ results files created:
 
Format:
[parent project/datset][ImageJ results] image id : image name : result filename
 
------------------------------------------------------------------------
%s""" % ("\n".join(image_names))))
 
    smtpObj = smtplib.SMTP('localhost')
    smtpObj.sendmail(ADMIN_EMAIL, [params['Email_address']], msg.as_string())
    smtpObj.quit()   

def execute_macro(conn, scriptParams):
    """ 
    Get the images and other data from scriptParams, then call the 
    process_image for each image, passing in other parameters as needed
    """
    
    global input_dir
    global output_dir
     
    macroId = scriptParams['AnnotationID']
    ann = conn.getObject("Annotation",macroId)
    if not ann:
        message = 'Could not find specified annotation'
        return message
    
    macro_name,macro_path = download_macro(ann)
    macro_args = scriptParams['Macro_Args']
# 
#     cIndex = scriptParams['Channel_To_Analyse'] - 1     # Convert to zero-based index
#     use_rois = scriptParams['Analyse_ROI_Regions']
    if scriptParams['Save_ImageJ_Log']:
        results_file = scriptParams['Result_File_Name']
    else:
        results_file = None
         
    if scriptParams['New_Dataset']:
        datasetName = scriptParams['Container_Name']
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        dataset = conn.getUpdateService().saveAndReturnObject(dataset)
    else:
        dataset=None
 
    input_dir = tempfile.mkdtemp(prefix='imagej_input')
    output_dir = tempfile.mkdtemp(prefix='imagej_output')
 
    def empty_dir(dir_path):
        for old_file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, old_file)
            os.unlink(file_path)
     
    new_image_ids = []
    results_files = []
    for image in conn.getObjects("Image", scriptParams['IDs']):
 
        # remove input and processed images
        empty_dir(input_dir)
        empty_dir(output_dir)
 
        robj, message, results, newIds = process_image(conn, image, macro_path, macro_args, results_file, dataset)
        new_image_ids.append(newIds)
        results_files.append(results)
         
    if scriptParams['Email_Results'] and new_image_ids:
        email_results(conn,scriptParams,new_image_ids,results_files)
    elif scriptParams['Email_Results']:
        image_ids = scriptParams['IDs']
        email_results(conn,scriptParams,image_ids,results_files)
         
    try:
        os.remove(input_dir)
        os.remove(output_dir)
        os.remove(mpath)
        print 'deleted all temporary folders'
    except OSError:
        pass
 
#     # Handle what we're returning to client
#     if len(newImages) == 0:
#         return None, "No images created"
#     if len(newImages) == 1:
#         new = newImages[0]
#         msg = "New Image: %s" % new.getName()
#         return new._obj, msg
#     else:
#         ds = newImages[0].getParent()
#         if ds is not None:
#             return ds._obj, "%s New Images in Dataset:" % len(newImages)
#         else:
#             return None, "Created %s New Images" % len(newImages)
   
    return robj,message

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

def runScript():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters. 
    """
       
    dataTypes = [rstring('Image')]
     
    client = scripts.client('ImageJ_Processing.py',
"""
This script attempts to execute an ImageJ macro that has been saved to the server as an annotation.
""",

    scripts.String("Data_Type", optional=False, grouping="1",
        description="The data you want to work with.", values=dataTypes, default="Image"),

    scripts.List("IDs", optional=False, grouping="2",
        description="List of Dataset IDs or Image IDs").ofType(rlong(0)),
                            
    scripts.Int("AnnotationID", optional=False, grouping="3",
        description="ID of imagej macro to execute"),
                               
    scripts.List("Macro_Args", optional=True, grouping="4",
        description="An optional list of arguments to be passed to the macro").ofType(rstring("")),

    scripts.Bool("Save_ImageJ_Log", grouping="5", optional=False, default=False,
        description="Use this to capture any output sent to the ImageJ log."),
                            
    scripts.String("Result_File_Name", grouping="5.1", default='ImageJ_result.txt',
        description="Name of file in which to save the ImageJ log (*.txt). Will be annotated to the image ID above."), 
                            
    scripts.Bool("New_Dataset", grouping="6",default=False,
        description="Put results in new dataset? Only do this if the macro creates new images"),
                                                        
    scripts.String("Container_Name", grouping="6.1",
        description="Option: put Images in new Dataset with this name",
        default="ImageJ_results"),
                            
    scripts.Bool("Email_Results", grouping="7", default=False,
        description="E-mail the results"),
                            
    scripts.String("Email_address", grouping="7.1", description="Specify e-mail address"),                            

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
        print scriptParams
        
        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return
   
        robj, message = execute_macro(conn, scriptParams)
   
        client.setOutput("Message", rstring(message))
        if robj is not None:
            client.setOutput("Result", robject(robj))

    finally:
        client.closeSession()

if __name__ == "__main__":
    runScript()
    