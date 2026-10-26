from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checks', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='check',
            name='error_message',
            field=models.TextField(blank=True, default=''),
        ),
    ]
