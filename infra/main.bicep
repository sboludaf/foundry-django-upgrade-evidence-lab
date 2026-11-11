targetScope = 'subscription'
param environmentName string
param location string = 'westeurope'
param resourceGroupName string = 'rg-${environmentName}'
param operatorPrincipalId string
param provisionGrounding string = 'true'
@description('Planned retirement timestamp. This tag does not schedule deletion.')
param cleanupAt string
@description('End of paid analyses; defaults to the retirement deadline.')
param labExpiresAt string = cleanupAt
param labId string = 'foundry-django-upgrade-evidence'
param proposalHash string = ''
@description('For an existing installation, keep its current real image during provision.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
@allowed(['6', '12', '16', '24'])
param modelCallLimit string = '12'
@allowed(['3', '6', '12'])
param bingQueryLimit string = '6'
@secure()
param djangoSecret string = '${newGuid()}${newGuid()}'
var tags = union({
  'azd-env-name': environmentName
  'lab-id': labId
  'cleanup-at': cleanupAt
}, empty(proposalHash) ? {} : {
  'proposal-hash': proposalHash
})
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}
module core './core.bicep' = {
  name: 'django-core'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: tags
    operatorPrincipalId: operatorPrincipalId
    provisionGrounding: provisionGrounding
  }
}
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_AI_ACCOUNT_NAME string = core.outputs.accountName
output AZURE_AI_PROJECT_ENDPOINT string = core.outputs.projectEndpoint
output AZURE_AI_PROJECT_ID string = core.outputs.projectId
output AZURE_AI_ENDPOINT string = core.outputs.endpoint
output AZURE_AI_MODEL_DEPLOYMENT_NAME string = 'django-luna'
output AZURE_DECISION_DEPLOYMENT string = 'django-decision'
output BING_CONNECTION_ID string = core.outputs.bingConnectionId
output BING_CUSTOM_SEARCH_INSTANCE_NAME string = core.outputs.bingInstanceName
output BING_RESOURCE_ID string = core.outputs.bingId

module web './web.bicep' = {
  name: 'django-web'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: tags
    accountName: core.outputs.accountName
    projectEndpoint: core.outputs.projectEndpoint
    bingConnectionId: core.outputs.bingConnectionId
    operatorPrincipalId: operatorPrincipalId
    djangoSecret: djangoSecret
    modelCallLimit: modelCallLimit
    bingQueryLimit: bingQueryLimit
    labExpiresAt: labExpiresAt
    containerImage: containerImage
  }
}
output AZURE_CONTAINER_REGISTRY_NAME string = web.outputs.registryName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = web.outputs.registryEndpoint
output SERVICE_WEB_RESOURCE_NAME string = web.outputs.appName
output SERVICE_WEB_ENDPOINT_URL string = web.outputs.appUrl
output AZURE_STORAGE_ACCOUNT string = web.outputs.storageName
output AZURE_CLIENT_ID string = web.outputs.identityClientId
output AZURE_CONTAINER_APP_ENVIRONMENT_NAME string = web.outputs.environmentName
output AZURE_CONTAINER_APP_ENVIRONMENT_ID string = web.outputs.environmentId
output LAB_EXPIRES_AT string = labExpiresAt
