#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
import os
import re
import tempfile
from numpy import zeros
import glob
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from email.Utils import formatdate
from tifffile import TiffFile
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
    
def upload_reconstructed(conn,original_image,image_list,dataset):
    """
    This creates a new Image in OMERO using tifffile
    
    @param conn:             the BlitzGateway connection
    @param original_image:   the data being analysed
    @param image_list:       a list which contains the path of the image being uploaded
    @param dataset:       the destination dataset for the recontructed image
    """

    for i in image_list:
        imageName = i
        print 'new image path:',os.path.join(output_dir, i) 
        tif = TiffFile(os.path.join(output_dir, i))
        record = tif.series[0]
        if tif.is_ome:
            sizeZ, sizeT, sizeC, image_height, image_width = record['shape']
        elif tif.is_rgb:
            image_height, image_width,sizeC = record['shape']
            sizeT = 1
            sizeZ = 1
        else:
            sizeT,image_height, image_width = record['shape']
            sizeZ = 1
            sizeC = 1   
                 
        print 'image sizes:',sizeZ,sizeC,sizeT   
        print 'is_rgb:',tif.is_rgb
        print 'tif shape:',tif.asarray().shape
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
        description = "Output from QuickPALM:\n Image ID: %d"\
                % original_image.getId()
        newImg = conn.createImageFromNumpySeq(plane_gen, basename,  sizeZ=sizeZ, sizeC=sizeC, sizeT=sizeT, 
                                              description=description, dataset=dataset)
        print( "New Image ID", newImg.getId())
    return newImg,newImg.getId()   
    
def run_imagej_macro(image_name, minSNR, maxFWHM,nm_per_pixel,results_file,sr_pix_size):
    """
    Here we set-up the ImageJ macro and run it from the command line.
    We need to know the path to ImageJ jar.
    The macro text is written to the temp folder that we're running the script in,
    and the path to the macro is passed to the command line.
    
    @param image_name:    filename of image being processed
    @param minSNR:        a QuickPALM parameter - min signal-to-noise ratio 
                          for detected molecule
    @param maxFWHM:       a QuickPALM parameter - maximum size for detected
                          molecule
    @param nm_per_pixel:  size of pixel in raw data
    @param results_file:  name of file to which localisations will be saved
    @param sr_pix_size:   a QuickPALM parameter - size of pixel in reconstructed
                          image
    """

    quickpalm_ijm = """
str=getArgument();
args=split(str,"*");
ippath=args[0];
if (ippath==""){
    exit("Argument is missing");
}
opname="reconstruction.tif";
oppath=args[1];
path = ippath;
open(path);
//name = File.getName(path);
//run("Bio-Formats Macro Extensions");
//Ext.setId(path);
//Ext.openImagePlus(path);

run("Analyse Particles", "minimum=%s maximum=%s image=%s"""\
""" smart online stream file=[%s] """\
"""pixel=%s accumulate=0 update=10 _image=imgNNNNNNNNN.tif start=0 in=50 _minimum=0"""\
""" local=20 _maximum=1000 threads=50");
saveAs("Tiff", oppath+opname);""" \
% (str(minSNR), str(maxFWHM),str(nm_per_pixel),results_file,str(sr_pix_size))

    ijm_path = "quickpalm.ijm"

    # write the macro to a known location that we can pass to ImageJ
    f = open(ijm_path, 'w')
    f.write(quickpalm_ijm)
    f.close()

    # Call ImageJ via command line, with macro ijm path & parameters
    print image_name
    print output_dir
    macro_args = "*".join( [image_name, output_dir+'/'])        # can't use ";" on Mac / Linu. Use "*"
#     cmd = "%s/ImageJ-macosx --memory=1000m --headless -macro %s %s -batch" % (IMAGEJPATH, ijm_path, macro_args)
    cmd = "%s/ImageJ-linux64 --memory=1000m --headless -macro %s %s -batch" % (IMAGEJPATH, ijm_path, macro_args)
    os.system(cmd)     

def run_quickpalm(conn,omero_image,dataset,minSNR,maxFWHM,sr_pix_size):
    """
    Launches the QuickPALM processing and uplaods results
    
    @param conn:          the BlitzGateWay connection
    @param omero_image:   the image being analysed
    @param dataset:       the destination dataset for the recontructed image
    @param minSNR:        a QuickPALM parameter - min signal-to-noise ratio 
                          for detected molecule
    @param maxFWHM:       a QuickPALM parameter - maximum size for detected
                          molecule
    @param sr_pix_size:   a QuickPALM parameter - size of pixel in reconstructed
                          image
    """ 
       
    message = ''
    images = glob.glob(input_dir + '/*.tif')
    basename = os.path.basename(omero_image.getName())
    if '.ome' in basename:
        basename = basename[:-4]
        
    pixels = omero_image.getPrimaryPixels()
    physicalSizeX = pixels.getPhysicalSizeX()*1000
    physicalSizeY = pixels.getPhysicalSizeY()*1000
            
    results_file = output_dir + "/" + basename[:-4] + "_particle_table.xls"
    
    run_imagej_macro(images[0], minSNR, maxFWHM, physicalSizeX, results_file, sr_pix_size)

    output_file = os.path.isfile(results_file)
    if output_file:
        new_image = glob.glob('%s/*.tif' % output_dir)
        print 'reconstructed image:',new_image
        parentDataset = None
        if new_image:
            if dataset is not None:
                parentDataset = dataset
            else:
                parentDataset = omero_image.getParent()
        # upload the reconstructed image and attach the result to the image
        recon,recon_id = upload_reconstructed(conn,omero_image,new_image,parentDataset)
        
        new_file_ann, faMessage = script_util.createLinkFileAnnotation(
            conn, results_file, recon, output="QuickPALM analysis file",
            mimetype="application/vnd.ms-excel", desc=None)
    else:
        faMessage = 'Some output files were missing'
        recon_id = None
        new_file_ann = None
    message += faMessage   
    return recon_id,new_file_ann,faMessage
    
def download_image(conn, image):
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
    
def run_processing(conn, script_params):
    """
    Collects params and starts the processing
    
    @param conn:          the BlitzGateWay connection
    @param script_params: the parameters collected from the script input
    """ 
        
    global input_dir
    global output_dir
        
    message = ""
    
    image_ids = script_params['IDs']
    if len(image_ids) > 5:
        message = 'Max number of datasets for batch exceeded (5)'
        return message
    
    for image in conn.getObjects("Image",image_ids):
        if not image:
            message = 'Could not find specified image'
            return message
        
    minSNR = script_params['minimum_SNR']
    maxFWHM = script_params['maximum_FWHM']
    sr_pix_size = script_params['SR_pixel_size']
        
    if script_params['New_Dataset']:
        datasetName = script_params['Container_Name']
        dataset = omero.model.DatasetI()
        dataset.name = rstring(datasetName)
        dataset = conn.getUpdateService().saveAndReturnObject(dataset)
    else:
        dataset=None
    

    input_dir = tempfile.mkdtemp(prefix='quickpalm_input')
    output_dir = tempfile.mkdtemp(prefix='quickpalm_output')
    
    def empty_dir(dir_path):
        for old_file in os.listdir(dir_path):
            file_path = os.path.join(dir_path, old_file)
            os.unlink(file_path)
    file_anns = []
    new_images = []
    for image in conn.getObjects("Image",image_ids):
        # remove input and processed images
        empty_dir(input_dir)
        empty_dir(output_dir)
        
        # download the image
        download_image(conn,image)
        
        new_image,new_file_ann,faMessage = run_quickpalm(conn,image,dataset,minSNR,maxFWHM,sr_pix_size)
        new_images.append(new_image)
        file_anns.append(new_file_ann)
        
    if not file_anns:
        faMessage += "No Analysis files created. See 'Info' or 'Error' for"\
            " more details"
    elif len(file_anns) > 1:
        faMessage += "Created %s QuickPALM files" % len(file_anns)    
    message += faMessage     
    
    if glob.glob('%s/*.xls'%output_dir) and script_params['Email_Results']:
        email_results(conn,script_params,image_ids,file_anns)
           
    delete_tmp(input_dir)
    delete_tmp(output_dir)
            
    return message

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

    dataTypes = [rstring('Image')]

    client = scripts.client('QuickPALM.py', """Run QuickPALM analysis on a batch images. MAXIMUM NUMBER OF DATASETS FOR BATCH IS FIVE!""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of images on which to set pixel size").ofType(rlong(0)),
                            
    scripts.Int("minimum_SNR", optional=False, grouping="03", default=4,
        description="smallest value of SNR to be considered for image reconstruction"),

    scripts.Int("maximum_FWHM", optional=False, grouping="04",default=5,
        description="maximum of object to be considered"),      
                            
    scripts.Int("SR_pixel_size", optional=False, grouping="05",default=30,
        description="pixel size in reconstructed image"),  
                          
    scripts.Bool("New_Dataset", grouping="06",default=False,
        description="Put results in new dataset? Only do this if the macro creates new images"),
                                                        
    scripts.String("Container_Name", grouping="06.1",
        description="Option: put Images in new Dataset with this name",
        default="ImageJ_results"),                                                
                            
    scripts.Bool("Email_Results", grouping="07", default=True,
        description="E-mail the results"),
                            
    scripts.String("Email_address", grouping="07.1", description="Specify e-mail address"),                                       
        
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

        if scriptParams['Email_Results'] and not validate_email(conn, scriptParams):
            client.setOutput("Message", rstring("No valid email address"))
            return
        
        # process images in Datasets
        message = run_processing(conn, scriptParams)
        client.setOutput("Message", rstring(message))
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()