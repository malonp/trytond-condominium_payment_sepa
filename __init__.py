##############################################################################
#
#    GNU Condo: The Free Management Condominium System
#    Copyright (C) 2016- M. Alonso <port02.server@gmail.com>
#
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


from trytond.pool import Pool

from .bank import *
from .company import *
from .condominium import *
from .configuration import *
from .party import *
from .payment import *


def register():
    Pool.register(
        Bank,
        BankAccount,
        BankAccountNumber,
        CheckMandatesList,
        Company,
        CondoPain,
        CondoParty,
        Group,
        GroupConfiguration,
        Mandate,
        MandateConfiguration,
        Party,
        Payment,
        Unit,
        module='condominium_payment_sepa',
        type_='model',
    )
    Pool.register(MandateReport, module='condominium_payment_sepa', type_='report')
    Pool.register(CheckMandates, PartyReplace, module='condominium_payment_sepa', type_='wizard')
