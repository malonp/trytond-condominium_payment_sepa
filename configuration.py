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


from trytond.model import ModelView, ModelSQL, ModelSingleton, fields


__all__ = ['GroupConfiguration', 'MandateConfiguration']


class GroupConfiguration(ModelSingleton, ModelSQL, ModelView):
    'Condominium Payment Group Configuration'
    __name__ = 'condo.payment.group.configuration'

    sepa_batch_booking_selection = fields.Selection(
        [(None, ''), ('1', 'Batch'), ('0', 'Per Transaction')], 'Default Booking', sort=False
    )
    sepa_batch_booking = fields.Function(fields.Boolean('Default Booking'), getter='get_sepa_batch_booking')
    sepa_charge_bearer = fields.Selection(
        [(None, ''), ('DEBT', 'Debtor'), ('CRED', 'Creditor'), ('SHAR', 'Shared'), ('SLEV', 'Service Level')],
        'Default Charge Bearer',
        sort=False,
    )


class MandateConfiguration(ModelSingleton, ModelSQL, ModelView):
    'Condominium SEPA Mandate Configuration'
    __name__ = 'condo.payment.sepa.mandate.configuration'

    type = fields.Selection([('recurrent', 'Recurrent'), ('one-off', 'One-off')], 'Type', sort=False)
    scheme = fields.Selection([('CORE', 'Core'), ('B2B', 'Business to Business')], 'Scheme', sort=False)
