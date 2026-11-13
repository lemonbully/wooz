# Hand-written: BillSignature becomes InvoiceDetails (the seller block of the
# subscription invoice). RenameModel/RenameField keep any signature already
# uploaded, and let `migrate` run without makemigrations' interactive
# "was this renamed?" prompts.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0017_billsignature'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='BillSignature',
            new_name='InvoiceDetails',
        ),
        migrations.AlterModelOptions(
            name='invoicedetails',
            options={
                'ordering': ('-created_at',),
                'verbose_name': 'invoice details',
                'verbose_name_plural': 'invoice details',
            },
        ),
        migrations.RenameField(
            model_name='invoicedetails',
            old_name='image',
            new_name='signature',
        ),
        migrations.AlterField(
            model_name='invoicedetails',
            name='signature',
            field=models.ImageField(blank=True, help_text='A transparent PNG works best. Shown about 56px tall on the bill.', upload_to='billing/signatures/'),
        ),
        migrations.AlterField(
            model_name='invoicedetails',
            name='is_active',
            field=models.BooleanField(default=True, help_text='The newest active invoice details are the ones used on bills.'),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='company_name',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='mobile_number',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='address',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='gstin',
            field=models.CharField(blank=True, default='', help_text='15 characters. The state below is detected from its first two digits.', max_length=15, verbose_name='GSTIN'),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='hsn_code',
            field=models.CharField(blank=True, default='998313', max_length=8, verbose_name='HSN code'),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='state',
            field=models.CharField(blank=True, default='', help_text='Filled automatically from the GSTIN. A buyer in this state is billed CGST 9% + SGST 9%; any other state IGST 18%.', max_length=60, verbose_name='State (place of supply)'),
        ),
        migrations.AddField(
            model_name='invoicedetails',
            name='terms',
            field=models.JSONField(blank=True, default=list, verbose_name='Terms & Conditions'),
        ),
    ]
