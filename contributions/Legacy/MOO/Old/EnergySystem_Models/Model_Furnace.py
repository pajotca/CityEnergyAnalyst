# -*- coding: utf-8 -*-
"""Model_Furnace_Condensing"""



""" INCLUDE """ 
# raise ModelError
#class exception form Thuy-An : ModelError



from scipy.interpolate import interp1d
import numpy as np

import globalVar as gV
reload (gV)

def Furnace_InVCost(P_design, Q_annual):
    """
    Calculates the cost of a Furnace
    based on Bioenergy 2020 (AFO) and POLYCITY Ostfildern 
    
    Excludes Operating and Labour Costs!
    
    
    Parameters
    ----------
    P_design : float
        Design Power of Furnace Plant (Boiler Thermal power!!)
        
    Q_annual : float
        annual thermal Power output
    
    Returns
    -------
    InvC_return : float
        total investment Cost for building the plant
    
    InvCa : float
        annualized investment costs in CHF including labour, operation and maintainance
        
    """
    InvC = 0.670 * gV.EURO_TO_CHF * P_design # 670 € /kW therm(Boiler) = 800 CHF /kW (A+W data) 

    Ca_invest =  (InvC * gV.Boiler_i * (1+ gV.Boiler_i) ** gV.Boiler_n / ((1+gV.Boiler_i) ** gV.Boiler_n - 1)) 
    Ca_maint = Ca_invest * gV.Boiler_C_maintainance
    Ca_labour =  gV.Boiler_C_labour / 1000000.0 * gV.EURO_TO_CHF * Q_annual 

    InvCa = Ca_invest + Ca_maint + Ca_labour
    
    return InvC, InvCa


    
    
    
    
    
def Furnace_eff(Q_load, Q_design, T_return_to_boiler, MOIST_TYPE):

    """
    Efficiency for Furnace Plant (Wood Chip  CHP Plant, Condensing Boiler)
    
    Minimum Part load regime: 30% of P_design
    Efficiencies based on LHV ! 
    
    Source: POLYCITY HANDBOOK 2012
    
    Valid for Q_design = 1-10 MW
    
    Parameters
    ----------
    Q_load : float
        Load of time step
        
    Q_max : float
        Design Load of Boiler
        
    up to 6MW_therm_out Capacity proven!
    = 8 MW th (burner) 
    
    
    
    Returns
    -------
    eta_therm : float
        thermal Efficiency of Furnace (Lower Heating Value), in abs. numbers

    eta_el : float
        electric efficiency of Furnace (LHV), in abs. numbers
    
    Q_aux : float
        auxillary power for Plant operation in W
            
    """
    
    phi = float(Q_load) / float(Q_design)

    
    """ Plant Thermal Efficiency """
    if phi > gV.Furn_min_Load:
            
        #Implement Curves provided by http://www.greenshootscontrols.net/?p=153
        x = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1] # part load regime, phi = Q / Q_max
        y = [0.77, 0.79, 0.82, 0.84, 0.845, 0.85, 0.853, 0.854, 0.855]
    
        
        # do the interpolation 
        eff_of_phi = interp1d(x, y, kind='linear')
    
        
        # get input variables
        #print phi
        eta_therm = eff_of_phi(phi)
    
    else: 
        eta_therm = 0
        #print "Furnace Boiler below minimum Power! 1"
        #raise ModelError
    
    """ Plant Electric Efficiency """ 
    if phi < gV.Furn_min_electric:
        eta_el = 0
        #print "Furnace Boiler below minimum Power! 2"
        
    else:    
        x = [2/7.0, 3/7.0, 4/7.0, 5/7.0, 6/7.0, 1] # part load regime, phi = Q / Q_max
        y = [0.025, 0.0625, 0.102, 0.127, 0.146, 0.147]
        eff_el_of_phi = interp1d(x, y, kind='cubic')
        eta_el = eff_el_of_phi(phi)
    


    # Return Temperature Dependency
    
    x = [0,15.5, 21, 26.7, 32.2, 37.7, 43.3, 49, 54.4, 60, 65.6, 71.1, 100] # Return Temperature Dependency
    y = [96.8,96.8, 96.2, 95.5, 94.7, 93.2, 91.2, 88.9, 87.3, 86.3, 86.0, 85.9, 85.8] # Return Temperature Dependency
    eff_of_T_return = interp1d(x, y, kind='linear')
    
    
    eff_therm_tot = eff_of_T_return(T_return_to_boiler - 273) * eta_therm / eff_of_T_return(60)
    
    if MOIST_TYPE == "dry":
        eff_therm_tot = eff_of_T_return(T_return_to_boiler - 273) * eta_therm / eff_of_T_return(60) + 0.087 # 8.7 % efficiency gain when using dry fuel
        #print eff_therm_tot
        eta_el += 0.087
        
    Q_therm_prim = Q_load / eff_therm_tot
    
    Q_aux = gV.Boiler_P_aux * Q_therm_prim # 0.026 Wh/Wh= 26kWh_el/MWh_th_prim, 
    
    return eff_therm_tot, eta_el, Q_aux
    

""" for Plots !! """
"""
Q_load = 10
Q_design = 10


x = [2/7.0, 3/7.0, 4/7.0, 5/7.0, 6/7.0, 1] # part load regime, phi = Q / Q_max
y = [0.01, 0.0625, 0.102, 0.13, 0.146, 0.147]
eff_el_of_phi = interp1d(x, y, kind='cubic')

    # do the interpolation 
eff_of_phi = interp1d(x, y, kind='cubic')

# get input variables
phi = Q_load / Q_design

#boiler_eff = eff_of_phi(phi)


xnew = np.linspace(np.amin(x), np.amax(x), 1000)
import matplotlib.pyplot as plt

#plt.plot(xnew, f(xnew), label='linear')
plt.plot(xnew, eff_of_phi(xnew), label='cubic')
plt.legend(['Electric Efficiency = f(phi)'], loc='best')
plt.xlabel("Part Load phi = Q / Q_max ")
plt.ylabel("Electric Efficiency at Part Load")
plt.xlim(np.amin(x), np.amax(x))
plt.ylim(0,0.15)
plt.show()
"""
