import tempfile
from tqdm.auto import tqdm

import matplotlib.pyplot as plt
import numpy as np
from collections import OrderedDict
from shapely.geometry import box
from shapely.ops import clip_by_rect
from femwell.mesh import mesh_from_OrderedDict

from skfem import Mesh, Basis, ElementTriP0

from femwell.mode_solver import compute_modes, calculate_te_frac

if __name__ == '__main__':
    wavelength = 1.55
    num_modes = 8
    widths = np.linspace(.5, 3.5, 100)

    all_lams = np.zeros((widths.shape[0], num_modes))
    all_te_fracs = np.zeros((widths.shape[0], num_modes))
    for i, width in enumerate(tqdm(widths)):
        core = box(0, 0, width, .5)
        polygons = OrderedDict(
            core=core,
            box=clip_by_rect(core.buffer(1., resolution=4), -np.inf, -np.inf, np.inf, 0),
            clad=clip_by_rect(core.buffer(1., resolution=4), -np.inf, 0, np.inf, np.inf)
        )

        resolutions = dict(core={"resolution": 0.1, "distance": 1})

        with tempfile.TemporaryDirectory() as tmpdirname:
            mesh_from_OrderedDict(polygons, resolutions, filename=tmpdirname + '/mesh.msh', default_resolution_max=.6)
            mesh = Mesh.load(tmpdirname + '/mesh.msh')

        basis0 = Basis(mesh, ElementTriP0())
        epsilon = basis0.zeros() + 1
        epsilon[basis0.get_dofs(elements='core')] = 2 ** 2
        epsilon[basis0.get_dofs(elements='box')] = 1.444 ** 2

        lams, basis, xs = compute_modes(basis0, epsilon, wavelength=wavelength, mu_r=1, num_modes=num_modes)
        all_lams[i] = lams
        all_te_fracs[i, :] = [calculate_te_frac(basis, xs[idx]) for idx in range(num_modes)]

    all_lams = np.real(all_lams)
    plt.xlabel('Width of waveguide [µm]')
    plt.ylabel('Effective refractive index')
    plt.ylim(1.444, np.max(all_lams) + 0.1 * (np.max(all_lams) - 1.444))
    for lams, te_fracs in zip(all_lams.T, all_te_fracs.T):
        plt.plot(widths, lams)
        plt.scatter(widths, lams, c=te_fracs, cmap='cool')
    plt.show()
