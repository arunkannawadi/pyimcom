# usage: python truthcats.py <filter> <input prefix> <outstem>
# input file name is <input prefix><filter>_DD_DD_map.fits (block files)

import sys
import numpy
import healpy
import numpy as np
from astropy.io import fits
from astropy import wcs
from astropy.table import Table, vstack
from os.path import exists
import json
import re
import time

from .layer import GalSimInject, GridInject
from .config import Settings, Config
from .coadd import Block

def gen_truthcats(pars):
    """Generates a truth catalog. The argument is a list:

    [<name>, <filter>, <input prefix>, <outstem>]

    If <name> is not None, then reads WCS from the output file.
    If <name> is None, then the WCS is generated internally. In this case, only the
    starting block output file needs to exist.

    Filter should either be a letter or an integer. If it is a letter, then includes it in the input file name.

    If the outstem is '', then the output file name is generated according to the configuration file.

    """


    t0 = time.time()

    #bd = 40  # padding size
    #bd2 = 8

    nblockmax = 100  # maximum
    ncol = 22
    nstart = 0

    filter = pars[1]

    if isinstance(filter,int):
        filtername = Settings.RomanFilters[filter]
        filter = filtername[0]
        filter_ = ''
    else:
        if filter == 'Y': filtername = 'Y106'
        if filter == 'J': filtername = 'J129'
        if filter == 'H': filtername = 'H158'
        if filter == 'F': filtername = 'F184'
        if filter == 'K': filtername = 'K213'
        filter_ = filter

    # prefix and suffix
    in1 = pars[2]
    outstem = pars[3]
    if pars[3]: outfile_g = outstem + 'TruthCat_{:s}.fits'.format(filter)

    fullTables = {}

    for iblock in range(nstart, nblockmax ** 2):

        j = iblock
        ibx = j % nblockmax;
        iby = j // nblockmax

        # make sure that we can read either the legacy (_map.fits) or new (.fits) file names.
        infile = in1 + '{:s}_{:02d}_{:02d}_map.fits'.format(filter_, ibx, iby)
        if not exists(infile): infile = in1 + '{:s}_{:02d}_{:02d}.fits'.format(filter_, ibx, iby)
        if not exists(infile): continue

        print(f"INFILE: {infile}")

        # extract information from the header of the first file
        if iblock==nstart:
            infile_ = infile # save the first input file in the FITS header
            with fits.open(infile) as f:

                n = numpy.shape(f[0].data)[-1]  # size of output images

                config = ''
                for g in f['CONFIG'].data['text'].tolist(): config += g + ' '
                configStruct = json.loads(config)

                blocksize = int(configStruct['OUTSIZE'][0]) * int(configStruct['OUTSIZE'][1]) * float(
                    configStruct['OUTSIZE'][2]) / 3600. * numpy.pi / 180  # radians
                rs = 1.5 * blocksize / numpy.sqrt(2.)  # search radius
                n2 = int(configStruct['OUTSIZE'][1])  # will be used for coverage

                outscale = float(configStruct['OUTSIZE'][2])  # in arcsec
                # force_scale = .40 / outscale  # in output pixels <-- not used

                # padding region around the edge
                bdpad = int(configStruct['OUTSIZE'][1]) * int(configStruct['PAD'])

                # figure out which layer we want
                layers = [''] + configStruct['EXTRAINPUT']
                use_layers = {}
                print('# All EXTRAINPUT layers:', layers)
                for i in range(len(layers))[::-1]:
                    m = re.match(r'^gs\S*$', layers[i])
                    if m:
                        use_layers[str(m.group(0))] = i  # KL: later note: use re.split to separate at commas
                    m = re.match(r'^ns\S*$', layers[i])
                    if m:
                        use_layers[str(m.group(0))] = i

                CFG = Config(config)
                CFG.tempfile = None # the temporary directory for coaddition might not exist anymore
                if not pars[3]: outfile_g = CFG.outstem + '_TruthCat.fits'

        if pars[0] is not None:
            with fits.open(infile) as f:
                mywcs = wcs.WCS(f[0].header)
        else:
            B = Block(cfg=CFG,this_sub=ibx*CFG.nblock+iby,run_coadd=False)
            B.parse_config()
            mywcs = B.outwcs

        resolutionTables = {}  # re-initiate these to empty for each block, since we need to redo coords per block

        for layerName in use_layers.keys():
            print('LAYERNAME: ',layerName)

            params = re.split(r',', layerName)
            print('PARAMS:', params)
            m = re.search(r'(\D*)(\d*)', params[0])
            if m:
                res = int(m.group(2))
                this_res = str(res)

            if 'TRUTH'+str(res) not in fullTables.keys():  # This will only happen one time
                fullTables['TRUTH'+str(res)] = []

            if this_res not in resolutionTables.keys():  # This will happen every time we start a new block

                resolutionTables[this_res]=None

                # Calculate the coordinate information for this block
                if mywcs.pixel_n_dim==4:
                    ra_cent, dec_cent = mywcs.all_pix2world([(n - 1) / 2], [(n - 1) / 2], [0.], [0.], 0, ra_dec_order=True)
                else:
                    ra_cent, dec_cent = mywcs.all_pix2world([(n - 1) / 2], [(n - 1) / 2], 0, ra_dec_order=True)
                ra_cent = ra_cent[0];
                dec_cent = dec_cent[0]
                vec = healpy.ang2vec(ra_cent, dec_cent, lonlat=True)
                qp = healpy.query_disc(2 ** res, vec, rs, nest=False)
                ra_hpix, dec_hpix = healpy.pix2ang(2 ** res, qp, nest=False, lonlat=True)
                npix = len(ra_hpix)
                if mywcs.pixel_n_dim==4:
                    x, y, z1, z2 = mywcs.all_world2pix(ra_hpix, dec_hpix, numpy.zeros((npix,)), numpy.zeros((npix,)), 0)
                else:
                    x, y = mywcs.all_world2pix(ra_hpix, dec_hpix, 0)
                xi = numpy.rint(x).astype(numpy.int16);
                yi = numpy.rint(y).astype(numpy.int16)
                grp = numpy.where(numpy.logical_and(numpy.logical_and(xi >= bdpad, xi < n - bdpad),
                                                    numpy.logical_and(yi >= bdpad, yi < n - bdpad)))
                ra_hpix = ra_hpix[grp]
                dec_hpix = dec_hpix[grp]
                ipix = qp[grp]
                x = x[grp]
                y = y[grp]
                npix = len(x)

                newpos = numpy.zeros((npix, ncol))
                xi = numpy.rint(x).astype(numpy.int16)
                yi = numpy.rint(y).astype(numpy.int16)

                # Initiate table
                blockTable = Table()
                blockTable['Block'] = [r'{:02d}_{:02d}'.format(ibx, iby)] * npix
                blockTable['Layer'] = [layerName] * npix
                blockTable['Res_hpix'] = res
                # Position information
                blockTable['ra_hpix'] = ra_hpix
                blockTable['dec_hpix'] = dec_hpix
                blockTable['ipix'] = ipix
                blockTable['ibx'] = ibx
                blockTable['iby'] = iby
                blockTable['x'] = x
                blockTable['y'] = y
                blockTable['xi'] = xi
                blockTable['yi'] = yi
                blockTable['dx'] = dx = x - xi
                blockTable['dy'] = dy = y - yi

                # orientation angle (computed by finite difference @ +/- 1 arcsec)
                if mywcs.pixel_n_dim==4:
                    xPP, yPP, z1, z2 = mywcs.all_world2pix(ra_hpix, dec_hpix+1./3600., numpy.zeros((npix,)), numpy.zeros((npix,)), 0)
                    xMM, yMM, z1, z2 = mywcs.all_world2pix(ra_hpix, dec_hpix-1./3600., numpy.zeros((npix,)), numpy.zeros((npix,)), 0)
                else:
                    xPP, yPP = mywcs.all_world2pix(ra_hpix, dec_hpix+1./3600., 0)
                    xMM, yMM = mywcs.all_world2pix(ra_hpix, dec_hpix-1./3600., 0)
                pa_hpix = np.arctan2(xPP-xMM, yPP-yMM) * 180./numpy.pi
                pa_hpix -= 360.*np.floor(pa_hpix/360.)
                blockTable['pa_hpix'] = pa_hpix

                resolutionTables[this_res]=blockTable

            # default params
            seed = 4096
            shear = None

            icase = '{:d}'.format(use_layers[layerName])

            if 'gsext' in layerName:
                for param in params:
                    m = re.match(r'seed=(\d*)', param)
                    if m: seed = int(m.group(1))
                    m = re.match(r'shear=(\S*)', param)
                    if m: shear = m.group(1)

                truthcat = GalSimInject.genobj(12 * 4 ** res, ipix, 'exp1', seed)

                if shear is not None:
                    g_i = truthcat['g'][0, :] + truthcat['g'][1, :] * 1j
                    q_i = (1 - numpy.absolute(g_i)) / (1 + numpy.absolute(g_i))

                    apply_shear = re.split(r':', shear)
                    g_t = float(apply_shear[0]) + float(apply_shear[1]) * 1j
                    q_t = (1 - numpy.absolute(g_t)) / (1 + numpy.absolute(g_t))

                    g_f = (g_i + g_t) / (1 + numpy.conj(g_t) * g_i)  # transformations
                    r_f = truthcat['sersic']['r'][:] * numpy.sqrt(q_t / q_i)
                    truthcat['g'][0, :] = g_f.real  # update the catalog
                    truthcat['g'][1, :] = g_f.imag
                    truthcat['sersic']['r'][:] = r_f

                # Include results in the table (for gsext objects)
                resolutionTables[this_res]['sersic_r_L' + icase] = truthcat['sersic']['r'][:] # this needs to be able to get the right table
                resolutionTables[this_res]['g1_L' + icase] = truthcat['g'][0, :]
                resolutionTables[this_res]['g2_L' + icase] = truthcat['g'][1, :]

            elif 'gsfdstar' in layerName:
                for param in params:
                    m = re.match(r'[^a-zA-Z]+', param)
                    if m:
                        fdm_amp = m.group(0)
                resolutionTables[this_res]['fdm_amp_L' + icase] = fdm_amp

            # elif 'nstar' in layerName:
            #     args = params
            #     ns_ipix, ns_xsca, ns_ysca, ns_rapix, ns_decpix = GridInject.generate_star_grid(res, mywcs)
            #     resolutionTables[this_res]['ipix' + layerName] = ns_ipix
            #     resolutionTables[this_res]['xsca' + layerName] = ns_xsca
            #     resolutionTables[this_res]['ysca' + layerName] = ns_ysca
            #     resolutionTables[this_res]['rapix' + layerName] = ns_rapix
            #     resolutionTables[this_res]['decpix' + layerName] = ns_decpix

                # inside layer loop

        # inside block loop
        # At this point all the layers have been added, the block table is complete
        for key1 in resolutionTables.keys():
            for key2 in fullTables.keys():
                if key1 in key2:
                    fullTables[key2] = vstack([fullTables[key2],resolutionTables[key1]])

        # flush
        sys.stdout.flush()

    # Make the fits file
    phdu = fits.PrimaryHDU(np.zeros((2,2)))
    phdu.header['COMMENT'] = 'This is a trivial HDU. Truth tables for injected objects at various HEALPix resolutions' \
                     ' are contained in the following table HDUs.'
    hdul = fits.HDUList([phdu])

    for key in fullTables.keys():
        hdu = fits.BinTableHDU(data=fullTables[key])
        hdu.name = key
        hdu.header['RESOLUTI'] = key
        hdu.header['FILTER'] = filtername
        hdu.header['INBLKPTH'] = infile_
        for i in range(len(layers)):
            this_keyword = 'LYR{:d}'.format(i)
            hdu.header[this_keyword] = (layers[i], 'name of layer {:d}'.format(i))
        hdul.append(hdu)

    hdul.writeto(outfile_g, overwrite=True)
    print(f"FITS file '{outfile_g}' with truth tables created.")
    print(f"Time elapsed: {time.time()-t0}")

# usage from configuration file
def gen_truthcats_from_cfg(cfg):
    gen_truthcats([None, cfg.use_filter, cfg.outstem, None])

# stand-alone usage
if __name__ == '__main__':
    #gen_truthcats(sys.argv)
    gen_truthcats([None, 1, '/fs/scratch/PCON0003/cond0007/itertest2-out/itertest2_F', 'pyimcom/temp/test1'])
