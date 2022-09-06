'''Derived module from spod_base.py for standard SPOD.'''

# Import standard Python packages
import os
import sys
import time
import numpy as np
from tqdm import tqdm
from numpy import linalg as la
# from scipy import linalg as la
from pyspod.spod.base import Base
import pyspod.utils.parallel as utils_par



class Standard(Base):
	'''
	Class that implements a distributed batch version
	Spectral Proper Orthogonal Decomposition algorithm
	to the inputdata (for large datasets).

	The computation is performed on the `data` passed
	to the constructor of the `Standard` class, derived
	from the `Base` class.
	'''

	# @profile
	def fit(self, data, nt):
		'''
		Class-specific method to fit the data matrix using
		the SPOD low ram algorithm.
		'''
		start = time.time()

		## initialize data and variables
		self._initialize(data, nt)

		self._pr0(f' ')
		self._pr0(f'Calculating temporal DFT (parallel)')
		self._pr0(f'------------------------------------')

		# check if blocks are already saved in memory
		blocks_present = False
		if self._reuse_blocks:
			blocks_present = self._are_blocks_present(
				self._n_blocks, self._n_freq, self._blocks_folder, self._rank)

		# loop over number of blocks and generate Fourier realizations,
		# if blocks are not saved in storage
		self._Q_hat_f = dict()
		size_Q_hat = [self._n_freq, self._data[0,...].size, self._n_blocks]
		Q_hat = np.empty(size_Q_hat, dtype=complex)
		## check if blocks already computed or not
		if blocks_present:
			# load blocks if present
			size_Q_hat = [self._n_freq, *self._xshape, self._n_blocks]
			Q_hat = np.empty(size_Q_hat, dtype=complex)
			for i_blk in tqdm(range(0, self._n_blocks), desc='loading blocks'):
				self._Q_hat_f[str(i_blk)] = dict()
				for i_freq in range(0, self._n_freq):
					file = os.path.join(self._blocks_folder,
						'fft_block{:08d}_freq{:08d}.npy'.format(i_blk,i_freq))
					s = np.load(file)
					Q_hat[i_freq,...,i_blk] = np.load(file)
					self._Q_hat_f[str(i_blk)][str(i_freq)] = file
			Q_hat = utils_par.distribute_dimension(
				data=Q_hat, maxdim_idx=self._maxdim_idx+1, comm=self._comm)
			shape = [Q_hat.shape[0], Q_hat[0,...,0].size, Q_hat.shape[-1]]
			Q_hat = np.reshape(Q_hat, shape)
		else:
			# loop over number of blocks and generate Fourier realizations
			size_Q_hat = [self._n_freq, self._data[0,...].size, self._n_blocks]
			Q_hat = np.empty(size_Q_hat, dtype=complex)
			for i_blk in range(0,self._n_blocks):
				st = time.time()

				# compute block
				Q_blk_hat, offset = self._compute_blocks(i_blk)

				# save FFT blocks in storage memory
				self._Q_hat_f[str(i_blk)] = dict()
				for i_freq in range(0, self._n_freq):
					if self._savefft == True:
						Q_blk_hat_fr = Q_blk_hat[i_freq,:]
						file = f'fft_block{i_blk:08d}_freq{i_freq:08d}'
						# file = 'fft_block{:08d}_freq{:08d}.npy'.format(
							# i_blk,i_freq)
						path = os.path.join(self._blocks_folder, file)
						self._Q_hat_f[str(i_blk),str(i_freq)] = path
						shape = [*self._xshape]
						if self._comm: shape[self._maxdim_idx] = -1
						Q_blk_hat_fr.shape = shape
						utils_par.npy_save(
							self._comm,
							path,
							Q_blk_hat_fr,
							axis=self._maxdim_idx)

				# print info file
				self._pr0(f'block {(i_blk+1)}/{(self._n_blocks)}'
						  f' ({(offset)}:{(self._n_dft+offset)});  '
						  f'Elapsed time: {time.time() - st} s.')

				## store FFT blocks in RAM
				Q_hat[:,:,i_blk] = Q_blk_hat

		self._pr0(f'------------------------------------')
		self._pr0(f'Time to compute DFT: {time.time() - start} s.')
		if self._comm: self._comm.Barrier()
		start = time.time()

		# Loop over all frequencies and calculate SPOD
		self._pr0(f' ')
		self._pr0(f'Calculating SPOD (parallel)')
		self._pr0(f'------------------------------------')
		self._eigs = np.zeros([self._n_freq,self._n_blocks], dtype=complex)

		## compute standard spod
		self._compute_standard_spod(Q_hat)

		# store and save results
		self._store_and_save()
		self._pr0(f'------------------------------------')
		self._pr0(f' ')
		self._pr0(f'Results saved in folder {self._savedir_sim}')
		self._pr0(f'Time to compute SPOD: {time.time() - start} s.')
		if self._comm: self._comm.Barrier()
		return self


	# @profile
	def _compute_blocks(self, i_blk):
		'''Compute FFT blocks.'''
		# get time index for present block
		offset = min(i_blk * (self._n_dft - self._n_overlap) \
			+ self._n_dft, self._nt) - self._n_dft

		# Get data
		Q_blk = self._data[offset:self._n_dft+offset,...]
		Q_blk = Q_blk.reshape(self._n_dft, self._data[0,...].size)

		# Subtract longtime or provided mean
		Q_blk = Q_blk[:] - self._t_mean

		# if block mean is to be subtracted,
		# do it now that all data is collected
		if self._mean_type.lower() == 'blockwise':
			Q_blk = Q_blk - np.mean(Q_blk, axis=0)

		# normalize by pointwise variance
		if self._normalize_data:
			Q_var = np.sum(
				(Q_blk - np.mean(Q_blk, axis=0))**2, axis=0) / (self._n_dft-1)
			# address division-by-0 problem with NaNs
			Q_var[Q_var < 4 * np.finfo(float).eps] = 1;
			Q_blk = Q_blk / Q_var
		Q_blk = Q_blk * self._window
		Q_blk_hat = (self._win_weight / self._n_dft) * np.fft.fft(Q_blk, axis=0)
		Q_blk_hat = Q_blk_hat[0:self._n_freq,:];

		# correct Fourier coefficients for one-sided spectrum
		if self._isrealx:
			Q_blk_hat[1:-1,:] = 2 * Q_blk_hat[1:-1,:]
		return Q_blk_hat, offset

	# @profile
	def _compute_standard_spod(self, Q_hat):
		'''Compute standard SPOD.'''
		# compute inner product in frequency space, for given frequency

		st = time.time()
		M = [None]*self._n_freq
		for f in range(0,self._n_freq):
			Q_hat_f = np.squeeze(Q_hat[f,:,:])#.astype(complex)
			M[f] = Q_hat_f.conj().T @ (Q_hat_f * self._weights) / self._n_blocks
		M = np.stack(M)
		M = utils_par.allreduce(data=M, comm=self._comm)
		self._pr0(f'- M computation: {time.time() - st} s.')
		st = time.time()

		## compute eigenvalues and eigenvectors
		L, V = la.eig(M)
		L = np.real_if_close(L, tol=1000000)

		# reorder eigenvalues and eigenvectors
		for f, Lf in enumerate(L):
			idx = np.argsort(Lf)[::-1]
			L[f,:] = L[f,idx]
			vf = V[f,...]
			vf = vf[:,idx]
			V[f] = vf
		self._pr0(f'- Eig computation: {time.time() - st} s.')
		st = time.time()

		# compute spatial modes for given frequency
		L_diag = 1. / np.sqrt(L) / np.sqrt(self._n_blocks)
		V_hat = V * L_diag[:,None,:]
		# phi = [None] * self._n_freq
		for f in range(0,self._n_freq):
			s0 = time.time()
			st = time.time()
			## compute
			phi = np.matmul(Q_hat[f,...], V[f,...] * L_diag[f,None,:])
			phi = phi[...,0:self._n_modes_save]
			# self._pr0(f'- compute: {time.time() - st} s.')
			# st = time.time()

			## save modes
			filename = f'freq_idx_{f:08d}.npy'
			p_modes = os.path.join(self._file_modes, filename)
			shape = [*self._xshape,self._nv,self._n_modes_save]
			if self._comm:
				shape[self._maxdim_idx] = -1
			phi.shape = shape
			# self._pr0(f'- reshape: {time.time() - st} s.')
			# st = time.time()
			utils_par.npy_save(self._comm, p_modes, phi, axis=self._maxdim_idx)
			# self._pr0(f'- save: {time.time() - st} s.')
			# st = time.time()
			self._pr0(f'freq: {f}/{self._n_freq};  '
					  f'Elapsed time: {time.time() - s0} s.')

		self._pr0(f'- Modes computation  and saving: {time.time() - st} s.')

		# phi = np.stack(phi)
		# phi = phi[...,0:self._n_modes_save]

		# get eigenvalues and confidence intervals
		self._eigs = np.abs(L)
		fac_lower = 2 * self._n_blocks / self._xi2_lower
		fac_upper = 2 * self._n_blocks / self._xi2_upper
		self._eigs_c[...,0] = self._eigs * fac_lower
		self._eigs_c[...,1] = self._eigs * fac_upper
