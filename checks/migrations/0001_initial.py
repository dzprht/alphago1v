import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Check',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('text', 'Текст'), ('url', 'Ссылка'), ('audio', 'Аудио'), ('screenshot', 'Скриншот'), ('transfer', 'Перевод')], max_length=10)),
                ('status', models.CharField(choices=[('pending', 'Ожидает обработки'), ('completed', 'Завершена'), ('failed', 'Ошибка обработки')], default='pending', max_length=10)),
                ('input_data', models.JSONField(default=dict)),
                ('extracted_text', models.TextField(blank=True)),
                ('result', models.JSONField(blank=True, null=True)),
                ('context_snapshot', models.JSONField(blank=True, default=dict)),
                ('analyzer_version', models.CharField(blank=True, max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checks', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('kind__in', ['text', 'url', 'audio', 'screenshot', 'transfer'])), name='check_kind_valid'), models.CheckConstraint(condition=models.Q(('status__in', ['pending', 'completed', 'failed'])), name='check_status_valid')],
            },
        ),
    ]
