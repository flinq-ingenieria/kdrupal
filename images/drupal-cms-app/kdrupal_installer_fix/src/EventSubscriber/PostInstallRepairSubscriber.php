<?php

declare(strict_types=1);

namespace Drupal\kdrupal_installer_fix\EventSubscriber;

use Drupal\Core\Extension\ModuleExtensionList;
use Drupal\Core\Extension\ModuleHandlerInterface;
use Drupal\Core\Extension\ModuleInstallerInterface;
use Drupal\Core\Logger\LoggerChannelFactoryInterface;
use Drupal\Core\State\StateInterface;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;
use Symfony\Component\HttpKernel\Event\RequestEvent;
use Symfony\Component\HttpKernel\KernelEvents;

final class PostInstallRepairSubscriber implements EventSubscriberInterface {

  private const DONE_KEY = 'kdrupal_installer_fix.done';
  private const RUNNING_KEY = 'kdrupal_installer_fix.running';

  public function __construct(
    private readonly StateInterface $state,
    private readonly ModuleInstallerInterface $moduleInstaller,
    private readonly ModuleHandlerInterface $moduleHandler,
    private readonly ModuleExtensionList $moduleList,
    private readonly LoggerChannelFactoryInterface $loggerFactory,
  ) {
  }

  public static function getSubscribedEvents(): array {
    return [
      KernelEvents::REQUEST => ['onRequest', 512],
    ];
  }

  public function onRequest(RequestEvent $event): void {
    if (!$event->isMainRequest()) {
      return;
    }

    $path = $event->getRequest()->getPathInfo();
    if (str_starts_with($path, '/core/install.php') || str_starts_with($path, '/install.php')) {
      return;
    }

    if ($this->state->get(self::DONE_KEY) || $this->state->get(self::RUNNING_KEY)) {
      return;
    }

    $logger = $this->loggerFactory->get('kdrupal_installer_fix');
    $this->state->set(self::RUNNING_KEY, TRUE);

    try {
      foreach (['workflows', 'redirect', 'dashboard', 'navigation', 'gin_toolbar', 'dblog'] as $module) {
        if ($this->moduleHandler->moduleExists($module) || !$this->moduleList->exists($module)) {
          continue;
        }

        try {
          $this->moduleInstaller->install([$module], TRUE);
        }
        catch (\Throwable $exception) {
          $logger->warning('Could not install @module during post-install repair: @message', [
            '@module' => $module,
            '@message' => $exception->getMessage(),
          ]);
        }
      }

      drupal_flush_all_caches();
      $this->state->set(self::DONE_KEY, \Drupal::time()->getRequestTime());
      $logger->notice('KDrupal post-install repair completed.');
    }
    catch (\Throwable $exception) {
      $logger->error('KDrupal post-install repair failed: @message', [
        '@message' => $exception->getMessage(),
      ]);
    }
    finally {
      $this->state->delete(self::RUNNING_KEY);
    }
  }

}
