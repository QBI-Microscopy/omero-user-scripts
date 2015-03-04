#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time
import numpy as np
import pandas as pd
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
import os
import tempfile
import glob
import itertools

FILE_TYPES = {
               'localizer'     :{
                                 'numColumns': 12, 
                                 'name': 'localizer', 
                                 'frame': 'First frame',
                                 'intensity': 'Integrated intensity', 
                                 'precision': None, 
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

def write_to_visp(filename,localisations,ft,sizeC):
    
    x = ft['x_col']
    y = ft['y_col']
    z = ft['z_col']
    p = ft['precision']
    zp = ft['zprecision']
    i = ft['intensity']
    f = ft['frame']
    print 'x,y,z,p,zp,i,f:',x,y,z,p,zp,i,f
    
    if z is None:
        if p is not None:
            if sizeC > 1:
                vext = ['_chan01.2dlp','_chan02.2dlp']
            else:
                vext = ['.2dlp']
        else:
            if sizeC > 1:
                vext = ['_chan01.2d','_chan02.2d']
            else:
                vext = ['.2d']
                
    else:
        if zp is not None:
            if sizeC > 1:
                vext = ['_chan01.3dlp','_chan02.3dlp']
            else:
                vext = ['.3dlp']
        else:
            if sizeC > 1:
                vext = ['_chan01.3d','_chan02.3d']
            else:
                vext = ['.3d']
                
    for c in range(sizeC): 
        try:
            visp_name = filename[:-4] + vext[c] 
            with file(visp_name, 'w') as outfile:
                locs_df = localisations[c]
                if (p is not None) and (zp is not None):
                    data = locs_df.loc[:,[x,y,z,p,p,zp,i,f]].values
                elif (p is not None) and (zp is None):
                    data = locs_df.loc[:,[x,y,p,p,i,f]].values
#                     data = np.concatenate((coords[c,:,:],precision[:,:],precision[:,:],intensity[:,:],frames[:,:]),axis=1)
                else:
                    data = locs_df[:,[x,y,i,f]].values
#                     data = np.concatenate((coords[c,:,:],intensity[:,:],frames[:,:]),axis=1)
                np.savetxt(outfile, data[1:,:], fmt="%.4f",delimiter="\t", newline="\n")
        except ValueError:
            return 'failed to write visp file'
        finally:
            #f.close()
            message = 'successfully wrote %s' % visp_name
            return message,visp_name 

def download_data(ann):
    """
        Downloads the specified file to and returns the path on the server
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

def get_all_locs_in_chan(all_data,chan=0,chancol=None):

    if chancol:
        coords = all_data[all_data[chancol] == chan]
    else:
        coords = all_data
    return coords

def get_all_locs(all_data,sizeC,chancol,nm_per_pixel):
    """
        Returns a list of pandas dataframes for each channel
    """        
    coords = [] 
    for c in range(sizeC):
        coords.append(get_all_locs_in_chan(all_data,c,chancol)*nm_per_pixel)   
    return coords

def parse_sr_data(path,file_type):
    """
        Parses all the data in the file being processed,
        and returns the xy coords in a numpy array
    """

    header_row = file_type['header_row'] 
        
    num_lines = sum(1 for line in open(path))

    try:
        with open(path) as t_in:
            data = pd.read_csv(t_in,header=header_row,\
                               sep='\t',engine='c',\
                               skiprows=range(num_lines-50,num_lines),\
                               index_col=False,low_memory=False)  
    except:
        print 'there was a problem parsing localisation data'
        return None
    return data


def run_processing(conn, script_params):
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
    
    filetype = FILE_TYPES[script_params['File_Type']]
     
    path_to_ann = ann.getFile().getPath() + '/' + ann.getFile().getName()
    name,ext = os.path.splitext(path_to_ann)
    if ('txt' in ext) or ('csv' in ext):
        path_to_data = download_data(ann)
        
        #first parse the original file to get all the data
        data = parse_sr_data(path_to_data, filetype)
        
        if script_params['Convert_coordinates_to_nm']:
            nm_per_pixel = script_params['Parent_Image_Pixel_Size']
        else:
            nm_per_pixel = 1
        
        if 'zeiss2chan' in filetype['name']:
            sizeC = 2
            chancol = filetype['chan_col']
        else:
            sizeC = 1
            chancol = None
            
        locs = get_all_locs(data, sizeC, chancol, nm_per_pixel)

        message,visp_path = write_to_visp(path_to_data,locs,filetype,sizeC)
        
        new_file_ann, faMessage = script_util.createLinkFileAnnotation(
            conn, visp_path, image, output="Create ViSP file",
            mimetype="text/plain", desc=None)
        if new_file_ann:
            file_anns.append(new_file_ann)

        if not file_anns:
            faMessage = "ViSP file could not be created. See 'Info' or 'Error' for"\
                " more details"
        elif len(file_anns) > 1:
            faMessage = "Created %s new ViSP file" % len(file_anns)
        message += faMessage
        
        try:
            for name in glob.glob("%s/*" % PATH):
                os.remove(name)
        except:
            pass
    
    else:
        message = 'file annotation must be txt or csv'
        return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Image')]
    
    fileTypes = [k for k in FILE_TYPES.iterkeys()]

    client = scripts.client('Localisations_To_ViSP.py', """This is a utility script to convert single molecule localisation data files to the ViSP format (see Nature Methods
10, 689-690 (2013). Note you do not need to convert Zeiss data to nm.""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.Int("ImageID", optional=False, grouping="02",
        description="ID of super resolved image to process"),
        
    scripts.Int("AnnotationID", optional=False, grouping="03",
        description="ID of file to process"),
        
    scripts.String("File_Type", optional=False, grouping="04",
        description="Indicate the type of data being processed", values=fileTypes, default="zeiss2D"),
                        
    scripts.Bool("Convert_coordinates_to_nm", optional=False, grouping="05",
        description="Convert localisation coordinates to nm - DO NOT USE WITH ZEISS DATA", default=False),
                            
    scripts.Int("Parent_Image_Pixel_Size", grouping="05.1",
        description="Convert the localisation coordinates to nm (multiply by parent image pixel size)"),
        
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
    finally:
        client.closeSession()

if __name__ == "__main__":
    run_as_script()