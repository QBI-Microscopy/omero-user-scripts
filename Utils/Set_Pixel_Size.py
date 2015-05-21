#!/usr/bin/env python
# -*- coding: utf-8 -*-
import omero.scripts as scripts
import omero.util.script_utils as script_util
from omero.gateway import BlitzGateway
import omero
from omero.rtypes import *
from omero.model import LengthI
from omero.model.enums import UnitsLength

def run_processing(conn, script_params):
    message = ""
    
    image_ids = script_params['IDs']
    for image in conn.getObjects("Image",image_ids):
        if not image:
            message = 'Could not find specified image'
            return message
        
        print 'size z:',image.getSizeZ()
        physical_z = None
        if (image.getSizeZ() > 1.0):
            try:
                physical_z = script_params['Z_Pixel_Size']
            except:
                message = 'Looks like you have a z-stack - you should set a non-zero value for Z_Pixel_Size'
                return message
                
            
        physical_x = script_params['X_Pixel_Size']
        physical_y = script_params['Y_Pixel_Size']
        
        if (physical_x == 0.0) or (physical_y == 0.0):
            message = 'You should set a non-zero pixel size for both X and Y'
            return message
        
        # Get the BlitzGateway wrapped pixels and unwrap it
        pixelsWrapper = image.getPrimaryPixels()
        pixels = pixelsWrapper._obj
        
        # Update and save
        print "units",UnitsLength
        lx = LengthI(physical_x, UnitsLength.MICROMETER)
        ly = LengthI(physical_y, UnitsLength.MICROMETER)
        pixels.setPhysicalSizeX(lx)
        pixels.setPhysicalSizeY(ly)
        if physical_z:
            lz = LengthI(physical_z, UnitsLength.MICROMETER)
            pixels.setPhysicalSizeZ(lz)
        conn.getUpdateService().saveObject(pixels)
        message += 'Successfully set pixel size on image %s' %image.getName()
    return message

def run_as_script():
    """
    The main entry point of the script, as called by the client via the scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Image')]

    client = scripts.client('Set_Pixel_Size.py', """Use this utility if your image has been saved without scaling information (e.g. TIF from Zen Black)""",

    scripts.String("Data_Type", optional=False, grouping="01",
        description="Choose source of images (only Image supported)", values=dataTypes, default="Image"),
        
    scripts.List("IDs", optional=False, grouping="02",
        description="IDs of images on which to set pixel size").ofType(rlong(0)),
                            
    scripts.Float("X_Pixel_Size", optional=False, grouping="03.1",
        description="Pixel size in um (micro-metres"),
        
    scripts.Float("Y_Pixel_Size", optional=False, grouping="03.2",
        description="Pixel size in um (micro-metres"),
        
    scripts.Float("Z_Pixel_Size", optional=True, grouping="03.3",
        description="Pixel size in um (micro-metres"),            
        
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