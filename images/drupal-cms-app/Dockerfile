FROM drupal:11.3.8-php8.4-fpm-bookworm

ENV COMPOSER_ALLOW_SUPERUSER=1
ENV APP_DIR=/var/www/html/app

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git unzip curl mariadb-client ca-certificates nginx; \
    rm -rf /var/lib/apt/lists/*; \
    curl -sS https://getcomposer.org/installer -o /tmp/composer-setup.php; \
    php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer; \
    rm -f /tmp/composer-setup.php

RUN set -eux; \
    mkdir -p ${APP_DIR}; \
    if [ ! -f ${APP_DIR}/composer.json ]; then \
      composer create-project "drupal/cms:^2" ${APP_DIR} --no-interaction; \
      cd ${APP_DIR}; \
      composer require drupal/redirect drupal/klaro --no-interaction; \
      composer install --no-interaction; \
      composer drupal:recipe-unpack --no-interaction || true; \
    fi

COPY kdrupal_installer_fix ${APP_DIR}/web/modules/custom/kdrupal_installer_fix

RUN set -eux; \
    PROFILE_INFO="$(find ${APP_DIR}/web -path '*/drupal_cms_installer.info.yml' -print -quit)"; \
    test -n "$PROFILE_INFO"; \
    if ! grep -q 'kdrupal_installer_fix' "$PROFILE_INFO"; then \
      if grep -q '^dependencies:' "$PROFILE_INFO"; then \
        sed -i '/^dependencies:/a\  - kdrupal_installer_fix:kdrupal_installer_fix' "$PROFILE_INFO"; \
      else \
        printf '\ndependencies:\n  - kdrupal_installer_fix:kdrupal_installer_fix\n' >> "$PROFILE_INFO"; \
      fi; \
    fi

RUN set -eux; \
    mkdir -p ${APP_DIR}/web/sites/default/files ${APP_DIR}/config/sync; \
    chown -R www-data:www-data ${APP_DIR}; \
    chmod -R ug+rwX ${APP_DIR}/web/sites/default/files

WORKDIR /var/www/html/app

CMD ["/bin/sh", "-lc", "php-fpm -D && exec nginx -g 'daemon off;'"]
